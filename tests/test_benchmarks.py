"""The benchmark kit (benchmarks/run.py), run against its own fake CLIs (benchmarks/fake/agent.py).

The "owner" here is the test: its source repository, its FLEET_CONFIG and its HOME are sentinels the kit must
leave untouched, while each arm clones into the test's TMPDIR. Results go to a temporary directory, never into
the repository's benchmarks/results/. No real CLI, account or network is used.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "benchmarks" / "run.py"
EXAMPLE = ROOT / "benchmarks" / "cases" / "example" / "case.json"
HAS_TMUX = shutil.which("tmux") is not None
HAS_NODE = shutil.which("node") is not None
SCRUB = re.compile(r"(CLAUDE|CODEX|CMUX|TMUX|FLEET|FAKE_)")

_spec = importlib.util.spec_from_file_location("bench_run", RUNNER)
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)

PRICES = """checked = "test prices, not real ones"

[models."opus"]
input = 10.0
output = 50.0
cache_read = 1.0
"""


class BenchCase(unittest.TestCase):
    """A throwaway owner: a source repository with the files the example case's checks read, a sentinel
    FLEET_CONFIG and HOME, and TMPDIR where the arms make their clones."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source = self.root / "source"
        (self.source / "skill" / "references").mkdir(parents=True)
        shutil.copy(ROOT / "skill" / "references" / "commands.md", self.source / "skill" / "references")
        (self.source / "README.md").write_text("# owner repo\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                 "commit", "-qm", "base")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.tmp, self.home, self.results = self.root / "tmp", self.root / "home", self.root / "results"
        self.tmp.mkdir()
        self.home.mkdir()
        self.config = self.root / "owner-config.toml"
        self.config.write_text('[defaults]\nreview = "cross-heavy"\n')
        self.env = {k: v for k, v in os.environ.items() if not SCRUB.match(k)}
        self.env.update(HOME=str(self.home), TMPDIR=str(self.tmp), FLEET_CONFIG=str(self.config),
                        GIT_CONFIG_NOSYSTEM="1")
        self.before = self.owner_state()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.source, check=True, capture_output=True, text=True).stdout

    def case(self, **changes) -> Path:
        data = json.loads(EXAMPLE.read_text())
        data.update({"repo": str(self.source), "commit": self.commit, **changes})
        d = self.root / "cases" / "demo"
        d.mkdir(parents=True, exist_ok=True)
        (d / "case.json").write_text(json.dumps(data))
        return d

    def bench(self, *args: str, expected: int | None = 0, timeout: float = 240) -> str:
        res = subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.root, env=self.env, text=True,
                             capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        out = res.stdout + res.stderr
        if expected is not None:
            self.assertEqual(res.returncode, expected, out)
        return out

    def owner_state(self) -> dict:
        files = {str(p.relative_to(self.source)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(self.source.rglob("*")) if p.is_file()}
        return {"files": files, "status": self.git("status", "--porcelain"), "config": self.config.read_bytes(),
                "home": sorted(str(p) for p in self.home.rglob("*"))}

    def assert_owner_untouched(self) -> None:
        self.assertEqual(self.owner_state(), self.before)

    def assert_cleaned(self, out: str) -> None:
        self.assertEqual(list(self.tmp.iterdir()), [], "an arm's temporary directory is left")
        for socket in re.findall(r"tmux -L (fleet-bench-[\w-]+)", out):
            gone = subprocess.run(["tmux", "-L", socket, "list-sessions"], capture_output=True)
            self.assertNotEqual(gone.returncode, 0, f"tmux -L {socket} outlived the run")

    def result(self) -> dict:
        files = list(self.results.glob("*.json"))
        self.assertEqual(len(files), 1, files)
        return json.loads(files[0].read_text())


class Cases(unittest.TestCase):
    def test_the_example_case_loads(self):
        case = bench.load_case(str(EXAMPLE.parent))
        self.assertEqual((case["name"], case["size"], case["model"]["cli"]), ("example", "small", "claude"))
        self.assertEqual(len(case["commit"]), 40)
        self.assertTrue(any(not c.get("checks") for c in case["criteria"]), "the example shows an owner criterion")

    def test_a_bad_case_names_every_problem(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "case.json").write_text(json.dumps({
                "repo": "x", "commit": "", "ticket": "t", "size": "huge", "model": {"cli": "gemini"},
                "checks": [{"id": "a", "run": "true"}, {"id": "a", "run": "true"}],
                "criteria": [{"id": "c", "text": "t", "checks": ["nope"]}]}))
            with self.assertRaises(bench.BenchError) as e:
                bench.load_case(d)
        for phrase in ("commit must be", "size must be one of", "model must be", "check id a is used twice",
                       "criterion c names a check"):
            self.assertIn(phrase, str(e.exception))

    def test_a_relative_repo_is_read_against_the_case(self):
        with tempfile.TemporaryDirectory() as d:
            data = json.loads(EXAMPLE.read_text())
            data["repo"] = "../src"
            (Path(d) / "c").mkdir()
            (Path(d) / "c" / "case.json").write_text(json.dumps(data))
            self.assertEqual(bench.load_case(str(Path(d) / "c"))["repo"], str((Path(d) / "src").resolve()))


class CommandLines(unittest.TestCase):
    def case(self, cli="claude", checks=None):
        return {"model": {"cli": cli, "model": "m1", "effort": "high"}, "allow": ["Bash(npm test *)"],
                "checks": checks or [{"id": "u", "run": "python3 -m unittest"}]}

    def test_claude_gets_accept_edits_with_rules_for_its_checks_and_nobody_to_answer(self):
        argv = bench.agent_command(self.case(), "PROMPT")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertEqual(argv[-1], "PROMPT")  # last, after the flag that ends --allowedTools' list
        flags = dict(zip(argv[2:-1:2], argv[3:-1:2]))
        self.assertEqual(flags["--permission-mode"], "acceptEdits")
        self.assertEqual(flags["--permission-prompts"], "none")
        self.assertEqual(flags["--output-format"], "json")
        self.assertEqual(flags["--allowedTools"],
                         "Bash(python3 -m unittest),Bash(python3 -m unittest *),Bash(npm test *)")

    def test_a_compound_check_gets_a_rule_per_part_and_a_comma_gets_none(self):
        rules = bench.claude_rules(self.case(checks=[{"id": "a", "run": "make x | grep -q ok && echo y"},
                                                     {"id": "b", "run": "python3 -c 'print(1,2)'"}]), [])
        self.assertEqual(rules, "Bash(make x),Bash(grep -q ok),Bash(echo y),Bash(npm test *)")

    def test_codex_runs_in_its_workspace_write_sandbox_with_approvals_never(self):
        argv = bench.agent_command(self.case("codex"), "PROMPT")
        self.assertEqual(argv, ["codex", "exec", "--json", "-m", "m1", "-c", "model_reasoning_effort=high",
                                "-s", "workspace-write", "-c", "approval_policy=never", "PROMPT"])

    def test_both_prompts_carry_the_same_ticket_and_checks(self):
        case = bench.load_case(str(EXAMPLE))
        a = type("A", (), {"max_workers": 3, "budget_minutes": 20})()
        single, lead = bench.single_prompt(case), bench.lead_prompt(case, "/x/.claude/skills/fleet", a)
        tail = lambda p: p[p.index("The ticket:"):]
        self.assertEqual(tail(single), tail(lead))
        self.assertIn(bench.LEAD_MARK, lead)
        self.assertIn("max_workers 3 and max_wall_minutes 20", lead)
        self.assertNotIn("Fleet", single)


class FleetConfig(unittest.TestCase):
    def test_the_owner_config_is_layered_with_a_private_socket_and_the_budget_as_caps(self):
        with tempfile.TemporaryDirectory() as d:
            owner = Path(d) / "config.toml"
            owner.write_text('[defaults]\nreview = "cross-all"\nmax_workers = 50\n\n[providers.claude-co]\nenabled = true\n')
            before = owner.read_bytes()
            a = type("A", (), {"fake": False, "max_workers": 4, "budget_minutes": 30})()
            case = bench.load_case(str(EXAMPLE))
            saved, bench.fleet.USER_CONFIG = bench.fleet.USER_CONFIG, owner
            try:
                arm = bench.Arm("fleet", case, a, None)
                self.addCleanup(shutil.rmtree, arm.root, True)
                loaded = tomllib.loads(bench.fleet.toml_text(arm.fleet_config()))
            finally:
                bench.fleet.USER_CONFIG = saved
            self.assertEqual(owner.read_bytes(), before)
        d = loaded["defaults"]
        self.assertEqual((d["backend"], d["tmux_socket"], d["max_workers"], d["max_wall_minutes"], d["review"]),
                         ("tmux", arm.socket, 4, 30, "cross-all"))
        self.assertNotIn("workspace", d)
        self.assertTrue(loaded["providers"]["claude-co"]["enabled"])
        self.assertNotIn("extends", loaded["providers"]["claude-co"])
        self.assertEqual(loaded["providers"]["claude-co"]["env"], {"CLAUDE_CONFIG_DIR": "~/.claude-co"})


class Costs(unittest.TestCase):
    def prices(self, text=PRICES):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(text)
        self.addCleanup(os.unlink, fh.name)
        return bench.load_prices(fh.name)

    def test_a_complete_estimate(self):
        c = bench.cost([{"label": "agent", "model": "opus", "tokens": {"input": 1_000_000, "output": 100_000}}],
                       self.prices())
        self.assertEqual((c["usd"], c["complete"], c["missing"]), (15.0, True, []))

    def test_unknown_tokens_or_prices_are_listed_never_zero(self):
        p = self.prices()
        c = bench.cost([{"label": "lead", "model": "opus", "tokens": {"input": 1_000_000}},
                        {"label": "worker r", "model": "gpt", "tokens": {"input": 5}},
                        {"label": "worker w", "model": "opus", "tokens": None},
                        {"label": "worker x", "model": "opus", "tokens": {"cache_write": 9}}], p)
        self.assertEqual((c["usd"], c["complete"]), (10.0, False))
        self.assertEqual(c["missing"], ["worker r: no price for gpt", "worker w: tokens unknown",
                                        "worker x: no cache_write price for opus"])
        nothing = bench.cost([{"label": "agent", "model": "opus", "tokens": None}], p)
        self.assertIsNone(nothing["usd"])
        self.assertEqual(bench.cost([{"label": "a", "model": "opus", "tokens": {"input": 1}}], None)["missing"],
                         ["no price file"])

    def test_nothing_is_built_in_and_the_readme_template_does_not_parse(self):
        self.assertNotIn("PRICES", dir(bench))
        readme = (ROOT / "benchmarks" / "README.md").read_text()
        template = re.search(r"```toml\n(.*?)```", readme, re.S).group(1)
        with self.assertRaises(bench.BenchError):
            self.prices(template)
        with self.assertRaises(bench.BenchError):
            self.prices('[models."m"]\ninput = -1\n')


def arm(name, **kw):
    base = {"arm": name, "wall_s": 600.0, "timed_out": False,
            "checks": [{"id": "u", "passed": True}, {"id": "v", "passed": True}],
            "criteria": [{"id": "a", "passed": True}, {"id": "b", "passed": True}],
            "review_findings": None, "review_note": "no review: one agent",
            "cost": {"usd": 2.0, "complete": True, "missing": []}, "manual_fixes": 0}
    return base | kw


class Table(unittest.TestCase):
    def render(self, *results) -> str:
        with tempfile.TemporaryDirectory() as d:
            for n, r in enumerate(results):
                (Path(d) / f"case{n}-2026-11-05.json").write_text(json.dumps(r))
            return bench.render_table(Path(d))

    def doc(self, fleet_arm=None, single=None, **kw):
        arms = {k: v for k, v in (("fleet", fleet_arm), ("single", single)) if v}
        return {"case": "c", "size": "small", "date": "2026-11-05", "model": {"cli": "claude", "model": "opus",
                                                                            "effort": "high"},
                "commit": "a" * 40, "repo": "r", "kit": {"fleet_version": "x"}, "arms": arms} | kw

    def test_no_results_makes_no_claim(self):
        self.assertIn("No results yet. Fleet makes no speed, cost or quality claim", self.render())

    def test_losses_are_shown(self):
        text = self.render(self.doc(
            arm("fleet", wall_s=1200.0, review_findings=2, review_note="from 1 review report",
                cost={"usd": 5.0, "complete": True, "missing": []},
                checks=[{"id": "u", "passed": True}, {"id": "v", "passed": False}], manual_fixes=["fixed a test"]),
            arm("single")))
        self.assertIn("- wall time: **Fleet lost** (20.0 min against 10.0 min)", text)
        self.assertIn("- cost: **Fleet lost** (~$5.00 (estimate) against ~$2.00 (estimate))", text)
        self.assertIn("- checks passed: **Fleet lost** (1 of 2 against 2 of 2)", text)
        self.assertIn("- manual fixes: **Fleet lost** (1 against 0)", text)
        self.assertIn("- criteria passed: tie", text)
        self.assertIn("| Fleet | 2 of 2 | 1 of 2 | 2 (from 1 review report) | 20.0 min | ~$5.00 (estimate) | 1 |", text)
        self.assertIn("| One agent | 2 of 2 | 2 of 2 | none (no review: one agent) | 10.0 min | ~$2.00 (estimate) | 0 |",
                      text)

    def test_unknowns_are_never_zero_and_are_not_compared(self):
        unknown = arm("fleet", wall_s=None, review_findings=None, review_note="1 of 1 review reports have no "
                      "readable header", cost={"usd": None, "complete": False, "missing": ["no price file"]},
                      manual_fixes=None, criteria=[{"id": "a", "passed": True}, {"id": "b", "passed": None}])
        text = self.render(self.doc(unknown, arm("single", timed_out=True,
                                                 cost={"usd": 1.5, "complete": False, "missing": ["lead: tokens unknown"]})))
        row = next(line for line in text.splitlines() if line.startswith("| Fleet |"))
        self.assertEqual(row, "| Fleet | 1 of 2 (1 for the owner to mark) | 2 of 2 | unknown (1 of 1 review reports "
                              "have no readable header) | unknown | unknown (no price file) | not filled in yet (owner) |")
        self.assertIn("10.0 min (stopped at the budget)", text)
        self.assertIn("at least ~$1.50 (estimate; not priced: lead: tokens unknown)", text)
        for metric in ("criteria passed", "wall time", "cost", "manual fixes"):
            self.assertIn(f"- {metric}: not compared", text)

    def test_a_missing_arm_and_a_fake_run_are_labelled(self):
        text = self.render(self.doc(None, arm("single"), fake=True))
        self.assertIn("fake run: plumbing only, not a benchmark", text)
        self.assertIn("| Fleet | not run | not run | not run | not run | not run | not run |", text)
        self.assertIn("Not compared: only one arm has run.", text)


class PlanOnly(BenchCase):
    def test_without_yes_paid_it_prints_the_plan_and_runs_nothing(self):
        out = self.bench(str(self.case()), "--results", str(self.results))
        self.assertIn("single arm: clone into a fresh temporary directory", out)
        self.assertIn("--permission-mode acceptEdits --permission-prompts none <prompt below>", out)
        self.assertIn("setup --harness claude --scope project --root <tmp>/repo", out)
        self.assertIn("Nothing was run or written.", out)
        self.assertRegex(out, r"To run it: .*run\.py .*demo --arm both --yes-paid --budget-minutes 60 --max-workers 6")
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertFalse(self.results.exists())
        self.assert_owner_untouched()

    def test_the_shipped_example_plans_too(self):
        out = self.bench("example", "--arm", "single")
        self.assertIn("benchmark example (small): claude opus at effort high", out)
        self.assertNotIn("fleet arm", out)


class FakeRuns(BenchCase):
    @unittest.skipUnless(HAS_TMUX and HAS_NODE, "the fleet arm needs tmux and node")
    def test_fake_runs_both_arms_end_to_end_and_writes_every_column(self):
        prices = self.root / "prices.toml"
        prices.write_text(PRICES)
        out = self.bench(str(self.case()), "--fake", "--results", str(self.results), "--prices", str(prices))
        data = self.result()
        self.assertTrue(data["fake"])
        single, fl = data["arms"]["single"], data["arms"]["fleet"]
        for a in (single, fl):
            self.assertEqual(a["commit"], self.commit)
            self.assertEqual(a["exit"], 0, a["agent"])
            self.assertEqual([c["passed"] for c in a["checks"]], [True, True])
            self.assertEqual([c["passed"] for c in a["criteria"]], [True, True, None])
            self.assertIsNone(a["manual_fixes"])
        self.assertEqual(single["tokens"]["agent"], {"input": 12000, "output": 3000, "cache_read": 40000})
        self.assertEqual(single["cost"], {"usd": 0.31, "complete": True, "missing": [], "prices": str(prices)})
        self.assertEqual(fl["fleet_runs"], ["bench"])
        self.assertEqual((fl["tokens"]["workers_count"], fl["tokens"]["workers_unknown"]), (2, 1))
        self.assertEqual(fl["tokens"]["workers"], {"input": 8000, "output": 2000})  # the Claude writer's log
        self.assertEqual(fl["review_findings"], 1)
        self.assertFalse(fl["cost"]["complete"])  # the Codex reviewer's tokens are unknown
        self.assertEqual(fl["cost"]["missing"], ["worker review-t1: tokens unknown"])
        self.assertIn("fleet accept bench t1 --gate", fl["agent"]["message"])
        table = (self.results / "README.md").read_text()
        self.assertIn("fake run: plumbing only", table)
        self.assertRegex(table, r"\| Fleet \| 2 of 3 \(1 for the owner to mark\) \| 2 of 2 \| 1 \(from 1 review report\) "
                                r"\| \d+ s; workers \d+ s summed \| at least ~\$0\.\d+ \(estimate; not priced: worker "
                                r"review-t1: tokens unknown\) \| not filled in yet \(owner\) \|")
        self.assertRegex(table, r"\| One agent \| 2 of 3 \(1 for the owner to mark\) \| 2 of 2 \| none \(no review: "
                                r"one agent\) \| \d+ s \| ~\$0\.31 \(estimate\) \| not filled in yet \(owner\) \|")
        self.assertIn("tmux -L fleet-bench-", out)
        self.assert_cleaned(out)
        self.assert_owner_untouched()
        self.assertEqual(list((ROOT / "benchmarks" / "results").glob("*-fake.json")), [])

    def test_keep_keeps_the_clone_and_the_owner_marks_criteria_later(self):
        out = self.bench(str(self.case()), "--fake", "--arm", "single", "--keep", "--results", str(self.results))
        kept = Path(re.search(r"kept (\S+)", out).group(1))
        self.addCleanup(shutil.rmtree, kept, True)
        self.assertTrue((kept / "repo" / "tools" / "list_commands.py").is_file())
        self.assertEqual(subprocess.run(["git", "remote"], cwd=kept / "repo", capture_output=True,
                                        text=True).stdout, "")  # nothing it runs can push to the owner
        path = next(self.results.glob("*.json"))
        data = json.loads(path.read_text())
        data["arms"]["single"]["criteria"][2].update(passed=False, by="owner")
        data["arms"]["single"]["manual_fixes"] = ["renamed a variable"]
        path.write_text(json.dumps(data))
        self.bench("--table", "--results", str(self.results))
        self.assertIn("| One agent | 2 of 3 | 2 of 2 | none (no review: one agent) |",
                      (self.results / "README.md").read_text())
        self.assert_owner_untouched()

    def test_codex_single_arm(self):
        self.bench(str(self.case(model={"cli": "codex", "model": "gpt-test", "effort": "high"})), "--fake",
                   "--arm", "single", "--results", str(self.results))
        single = self.result()["arms"]["single"]
        self.assertEqual(single["command"][:2], ["codex", "exec"])
        self.assertEqual([c["passed"] for c in single["checks"]], [True, True])
        self.assertEqual(single["tokens"]["agent"], {"input": 12000, "output": 3000})

    @unittest.skipUnless(HAS_TMUX and HAS_NODE, "the fleet arm needs tmux and node")
    def test_a_failed_arm_cleans_up(self):
        out = self.bench(str(self.case(commit="0" * 40)), "--fake", "--results", str(self.results), expected=1)
        self.assertIn("[single] failed: git checkout", out)
        self.assertIn("[fleet] failed: git checkout", out)
        self.assertFalse(self.results.exists())
        self.assert_cleaned(out)
        self.assert_owner_untouched()

    def interrupt(self, arm: str) -> str:
        env = dict(self.env, FAKE_BENCH_STALL="1")
        proc = subprocess.Popen([sys.executable, str(RUNNER), str(self.case()), "--fake", "--arm", arm, "--results",
                                 str(self.results)], cwd=self.root, env=env, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        lines = []
        for line in proc.stdout:
            lines.append(line)
            if "agent started" in line:
                break
        self.assertTrue(lines and "agent started" in lines[-1], "".join(lines))
        proc.send_signal(signal.SIGINT)
        rest, _ = proc.communicate(timeout=120)
        out = "".join(lines) + rest
        self.assertEqual(proc.returncode, 130, out)
        self.assertIn("interrupted: cleaned up; no result written", out)
        self.assertFalse(self.results.exists())
        self.assert_cleaned(out)
        self.assert_owner_untouched()
        return out

    def test_ctrl_c_cleans_up_the_single_arm(self):
        self.interrupt("single")

    @unittest.skipUnless(HAS_TMUX and HAS_NODE, "the fleet arm needs tmux and node")
    def test_ctrl_c_cleans_up_the_fleet_arm_and_its_tmux_server(self):
        self.assertIn("tmux -L fleet-bench-", self.interrupt("fleet"))


class StopWorkers(unittest.TestCase):
    def test_the_fleet_arm_stops_workers_the_lead_left_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            arm = object.__new__(bench.Arm)
            arm.repo = Path(tmp) / "repo"
            arm.skill = Path(tmp) / "skill"
            (arm.skill / "bin").mkdir(parents=True)
            (arm.skill / "bin" / "fleet").write_text("")
            for run, state in (("live", "running"), ("done", "exited")):
                d = arm.repo / ".fleet" / "runs" / run
                d.mkdir(parents=True)
                (d / "run.json").write_text(json.dumps({"workers": {"01": {"state": state}}}))
            calls = []
            arm.fleet_cmd = lambda *args, **kw: calls.append(args) or (0, "")
            self.assertTrue(arm.stop_workers())
            self.assertEqual(sorted(calls), [("stop", "done", "--all", "--timeout", "10"),
                                             ("stop", "live", "--all", "--timeout", "10")])
            (arm.repo / ".fleet/runs/live/run.json").write_text(json.dumps({"workers": {"01": {"state": "exited"}}}))
            self.assertFalse(arm.stop_workers())

    def test_workers_are_stopped_before_cost_and_checks(self):
        source = RUNNER.read_text()
        run = source[source.index("    def run(self) -> dict:"):source.index("    def stop_agent(self)")]
        self.assertLess(run.index("self.stop_workers()"), run.index("self.fleet_cost(r)"))
        self.assertLess(run.index("self.stop_workers()"), run.index("self.run_checks()"))


class Packaging(unittest.TestCase):
    def test_the_kit_is_not_shipped_in_the_npm_package(self):
        files = json.loads((ROOT / "package.json").read_text())["files"]
        self.assertFalse([f for f in files if f.startswith("benchmarks") or f in ("*", "**")], files)

    def test_the_readme_links_the_kit_and_makes_no_claim(self):
        readme = (ROOT / "README.md").read_text()
        self.assertIn("[benchmark kit](benchmarks/README.md)", readme)
        self.assertIn("it has no results yet", readme)


if __name__ == "__main__":
    unittest.main()
