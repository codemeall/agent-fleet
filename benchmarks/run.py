#!/usr/bin/env python3
"""Fleet's benchmark kit: one ticket, run once with Fleet and once with one agent of the same model.

    python3 benchmarks/run.py <case> --arm fleet|single|both [--yes-paid | --fake] [--budget-minutes N]
                              [--max-workers N] [--prices file] [--keep] [--results dir]
    python3 benchmarks/run.py --table [--results dir]

Each arm clones the case's repository at its commit into a fresh temporary directory, runs its agent there, runs
the case's checks in that clone and records the result in <results>/<case>-<date>.json; the table in
<results>/README.md is then rebuilt from every result file. The owner's checkout, Fleet config and tmux server are
never written. Without --yes-paid it prints the plan and the exact commands and runs nothing. --fake runs both
arms against the fake CLIs in benchmarks/fake/, for CI: it proves the plumbing, not quality. See
benchmarks/README.md for the method and the fairness rules.
"""
import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

KIT = Path(__file__).resolve().parent
ROOT = KIT.parent
FLEET = ROOT / "skill" / "bin" / "fleet"
FAKE_AGENT = KIT / "fake" / "agent.py"
SIZES = ("small", "medium-multi-file", "hidden-dependency")
CLIS = ("claude", "codex")
ARMS = ("single", "fleet")
RUN = "bench"  # the run name the lead is told to use; the runner reads every run it finds anyway
SKILL_PATHS = {"claude": ".claude/skills/fleet", "codex": ".agents/skills/fleet"}  # bin/setup.js, project scope
EXCLUDES = (".fleet/", ".claude/skills/fleet/", ".agents/skills/fleet/")  # the arm's own files, not the change
CHECK_TIMEOUT_S = 600
PRICE_KEYS = ("input", "output", "cache_read", "cache_write")  # USD per million tokens, as fleet cost names them
LEAD_MARK = "Deliver it with Fleet"  # the fake tells a lead from a single agent by this

_loader = importlib.machinery.SourceFileLoader("fleet_bench_kit", str(FLEET))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
fleet = importlib.util.module_from_spec(_spec)
_loader.exec_module(fleet)


class BenchError(Exception):
    pass


# ---------- cases ----------

def case_path(arg: str) -> Path:
    """A case directory, its case.json, or a name under benchmarks/cases/."""
    p = Path(arg)
    if p.is_dir():
        p = p / "case.json"
    elif not p.exists() and not p.suffix:
        p = KIT / "cases" / arg / "case.json"
    if not p.is_file():
        raise BenchError(f"no case at {arg} (looked for {p})")
    return p.resolve()


def is_url(repo: str) -> bool:
    return bool(re.match(r"^[a-z][a-z0-9+.-]*://", repo)) or bool(re.match(r"^[\w.-]+@[\w.-]+:", repo))


def load_case(arg: str) -> dict:
    path = case_path(arg)
    try:
        case = json.loads(path.read_text())
    except ValueError as e:
        raise BenchError(f"{path}: not JSON: {e}") from None
    problems = []
    for key in ("repo", "commit", "ticket", "size"):
        if not isinstance(case.get(key), str) or not case[key].strip():
            problems.append(f"{key} must be a non-empty string")
    if case.get("size") not in SIZES:
        problems.append(f"size must be one of {', '.join(SIZES)}")
    model = case.get("model")
    if not isinstance(model, dict) or model.get("cli") not in CLIS or not model.get("model") or not model.get("effort"):
        problems.append(f"model must be {{cli: {' or '.join(CLIS)}, model, effort}}")
    checks = case.get("checks")
    if not isinstance(checks, list) or not checks:
        problems.append("checks must be a non-empty list of {id, run, timeout?}")
        checks = []
    ids = set()
    for n, c in enumerate(checks, 1):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("run"), str):
            problems.append(f"check {n} must have an id and a run command")
        elif c["id"] in ids:
            problems.append(f"check id {c['id']} is used twice")
        else:
            ids.add(c["id"])
    criteria = case.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        problems.append("criteria must be a non-empty list of {id, text, checks?}")
        criteria = []
    for n, c in enumerate(criteria, 1):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("text"), str):
            problems.append(f"criterion {n} must have an id and a text")
        elif any(x not in ids for x in c.get("checks", [])):
            problems.append(f"criterion {c['id']} names a check the case does not define")
    if not all(isinstance(r, str) for r in case.get("allow", [])):
        problems.append("allow must be a list of Claude permission rules")
    if problems:
        raise BenchError(f"{path}: " + "; ".join(problems))
    case = dict(case, name=case.get("name") or path.parent.name, path=str(path))
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", case["name"]):
        raise BenchError(f"{path}: the case name must be letters, numbers, ., - or _")
    if not is_url(case["repo"]) and not Path(case["repo"]).expanduser().is_absolute():
        case["repo"] = str((path.parent / case["repo"]).resolve())
    return case


def checks_text(case: dict) -> str:
    return "\n".join(f"- `{c['run']}`" for c in case["checks"])


def single_prompt(case: dict) -> str:
    return (f"You are working alone on one ticket in this repository. Make the change in the working tree and test "
            f"it. Do not commit, push or change Git history. Nobody can answer questions during this run: decide, "
            f"and say what you decided in your final message.\n\nThe ticket:\n\n{case['ticket'].strip()}\n\n"
            f"The result is judged by these checks, run from the repository root:\n{checks_text(case)}\n")


def lead_prompt(case: dict, skill: str, a) -> str:
    return (f"You are the lead for one benchmark ticket in this repository. {LEAD_MARK}: read the Fleet skill at "
            f"`{skill}/SKILL.md` and follow it. The Fleet runtime is `{skill}/bin/fleet`.\n\n"
            f"The owner approved this ticket, its plan and its budget before this run. Do not stop to ask for "
            f"confirmation; nobody can answer questions during this run. Decide, and record your decisions in the run.\n"
            f"- Name the run `{RUN}` and use the tmux backend: `{skill}/bin/fleet init {RUN} --backend tmux`.\n"
            f"- Workers use the providers in FLEET_CONFIG. The run's caps are max_workers {a.max_workers} and "
            f"max_wall_minutes {a.budget_minutes}; do not work around them.\n"
            f"- Do not commit, push or change Git history. Leave the change in the working tree.\n"
            f"- When the work is accepted, run `{skill}/bin/fleet stop {RUN} --all` and reply with a short summary.\n\n"
            f"The ticket:\n\n{case['ticket'].strip()}\n\n"
            f"The result is judged by these checks, run from the repository root:\n{checks_text(case)}\n")


# ---------- the agents' command lines (checked in benchmarks/README.md, "Command lines checked") ----------

def claude_rules(case: dict, extra: list[str]) -> str:
    """Claude allow rules for the check commands and the case's own `allow` rules, as one comma-separated
    argument, as the headless docs show. A plain command gets an exact rule and one for it with more arguments;
    Claude checks each part of a compound command on its own, so a check with |, ;, & or a redirect gets an exact
    rule per part. A check with a comma inside would split the list, so it gets none (the plan says so)."""
    rules = list(extra)
    for c in case["checks"]:
        run = c["run"].strip()
        if "," in run:
            continue
        parts = [x.strip() for x in re.split(r"\|\||&&|[|;&]", run) if x.strip()]
        if len(parts) == 1 and not re.search(r"[<>`$()]", run):
            rules += [f"Bash({run})", f"Bash({run} *)"]
        else:
            rules += [f"Bash({x})" for x in parts]
    rules += case.get("allow", [])
    return ",".join(dict.fromkeys(rules))


def agent_command(case: dict, prompt: str, extra_rules: list[str] | None = None) -> list[str]:
    """One headless run of the case's model with edits and the case's test commands allowed in its clone (the
    working directory) and nothing that needs a person: Claude in acceptEdits with nobody to answer prompts,
    Codex in its workspace-write sandbox with approvals never asked."""
    m = case["model"]
    if m["cli"] == "claude":
        return ["claude", "-p", "--output-format", "json", "--allowedTools", claude_rules(case, extra_rules or []),
                "--model", m["model"], "--effort", m["effort"], "--permission-mode", "acceptEdits",
                "--permission-prompts", "none", prompt]
    return ["codex", "exec", "--json", "-m", m["model"], "-c", f"model_reasoning_effort={m['effort']}",
            "-s", "workspace-write", "-c", "approval_policy=never", prompt]


def lead_rules(*skills: str) -> list[str]:
    """The lead may run Fleet's runtime, by its absolute or repository-relative path, with any arguments."""
    return [rule for s in skills for rule in (f"Bash({s}/bin/fleet *)", f"Bash(python3 {s}/bin/fleet *)")]


# ---------- tokens and cost ----------

def load_prices(path: str | None) -> dict | None:
    """The owner's price file: [models."<model>"] tables of USD per million tokens. Nothing is built in."""
    if not path:
        return None
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BenchError(f"--prices {path}: {e}") from None
    models = data.get("models")
    if not isinstance(models, dict) or not models:
        raise BenchError(f"--prices {path}: needs [models.\"<model>\"] tables of USD per million tokens")
    for name, p in models.items():
        bad = [k for k, v in p.items() if k not in PRICE_KEYS or not isinstance(v, (int, float)) or v < 0]
        if bad or not isinstance(p, dict):
            raise BenchError(f"--prices {path}: models.{name}: keys are {', '.join(PRICE_KEYS)}, each a number >= 0")
    return {"file": str(Path(path).resolve()), "note": data.get("checked"), "models": models}


def cost(segments: list[dict], prices: dict | None) -> dict:
    """An estimate from the owner's prices: the sum over segments whose tokens and prices are both known, and what
    could not be priced. complete only when nothing is missing."""
    if prices is None:
        return {"usd": None, "complete": False, "missing": ["no price file"], "prices": None}
    usd, missing = 0.0, []
    for s in segments:
        p = prices["models"].get(s.get("model") or "")
        if not s.get("tokens"):
            missing.append(f"{s['label']}: tokens unknown")
        elif p is None:
            missing.append(f"{s['label']}: no price for {s.get('model')}")
        elif any(n and k not in p for k, n in s["tokens"].items()):
            missing.append(f"{s['label']}: no {', '.join(k for k, n in s['tokens'].items() if n and k not in p)} "
                           f"price for {s['model']}")
        else:
            usd += sum(n * p[k] / 1e6 for k, n in s["tokens"].items())
    known = len(missing) < len(segments)
    return {"usd": round(usd, 4) if known else None, "complete": not missing, "missing": missing,
            "prices": prices["file"]}


def add_tokens(a: dict | None, b: dict | None) -> dict | None:
    if not a or not b:
        return a or b
    return {k: a.get(k, 0) + b.get(k, 0) for k in dict.fromkeys([*a, *b])}


# ---------- one arm ----------

def clean_env(base: dict) -> dict:
    """The owner's environment without a lead's session markers or a Fleet config of theirs."""
    return {k: v for k, v in base.items() if k not in fleet.LEAD_ENV and not k.startswith("FLEET_")}


def git(*args: str, cwd: Path | None = None, timeout: int = 600) -> str:
    res = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, capture_output=True, text=True,
                         timeout=timeout, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        raise BenchError(f"git {' '.join(args)} failed: {(res.stderr or res.stdout).strip()[:500]}")
    return res.stdout


class Arm:
    """One arm in its own temporary directory: <root>/repo (the clone), <root>/tmp (the agent's TMPDIR), and for
    the fleet arm <root>/config.toml (its FLEET_CONFIG) and a private tmux server. Cleaned up on success, failure
    and Ctrl-C, unless --keep."""

    def __init__(self, name: str, case: dict, a, prices: dict | None):
        self.name, self.case, self.a, self.prices = name, case, a, prices
        self.root = Path(tempfile.mkdtemp(prefix=f"fleet-bench-{name}-")).resolve()
        self.repo, self.tmp = self.root / "repo", self.root / "tmp"
        self.config = self.root / "config.toml"
        self.socket = f"fleet-bench-{self.root.name[-8:]}".replace("_", "-")
        self.proc: subprocess.Popen | None = None
        self.notes: list[str] = []
        self.tmux_started = False
        self.cli = case["model"]["cli"]
        self.skill = self.repo / SKILL_PATHS[self.cli]

    def say(self, text: str) -> None:
        print(f"[{self.name}] {text}", flush=True)

    # setup

    def setup(self) -> str:
        self.tmp.mkdir()
        git("clone", "--quiet", "--no-hardlinks", "--no-checkout", self.case["repo"], str(self.repo), timeout=1800)
        git("checkout", "--quiet", "--detach", self.case["commit"], cwd=self.repo)
        git("remote", "remove", "origin", cwd=self.repo)  # nothing the agent runs can push to the owner's remote
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as fh:
            fh.write("\n# fleet benchmark kit: the arm's own files\n" + "\n".join(EXCLUDES) + "\n")
        commit = git("rev-parse", "HEAD", cwd=self.repo).strip()
        if self.a.fake:
            (self.root / "home").mkdir()
            self.fakebin = self.root / "fakebin"
            self.fakebin.mkdir()
            for cli in CLIS:
                shim = self.fakebin / cli
                shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_AGENT}" {cli} "$@"\n')
                shim.chmod(0o755)
            plan = dict(self.case.get("fake", {}), model=self.case["model"]["model"],
                        effort=self.case["model"]["effort"], checks=self.case["checks"])
            (self.root / "fake-plan.json").write_text(json.dumps(plan, indent=2))
        if self.name == "fleet":
            self.install_skill()
            self.config.write_text(fleet.toml_text(self.fleet_config()))
        return commit

    def install_skill(self) -> None:
        node = shutil.which("node")
        if not node:
            raise BenchError("the fleet arm installs the skill with `node bin/fleet.js setup`; install Node.js 18+")
        res = subprocess.run([node, str(ROOT / "bin" / "fleet.js"), "setup", "--harness", self.cli, "--scope",
                              "project", "--root", str(self.repo)], capture_output=True, text=True, timeout=120)
        if res.returncode != 0 or not (self.skill / "bin" / "fleet").is_file():
            raise BenchError(f"skill setup failed: {(res.stderr or res.stdout).strip()[:500]}")

    def fleet_config(self) -> dict:
        """The owner's merged config (shipped providers only, under --fake: claude and codex, both fakes) on this
        arm's private tmux server, with the budget as the run's caps."""
        if self.a.fake:
            cfg = fleet.smoke_config(fleet.load_config(Path("/nonexistent/fleet-config.toml")), list(CLIS), self.socket)
        else:
            cfg = fleet.load_config(fleet.USER_CONFIG)
            cfg = {"defaults": dict(cfg["defaults"]),  # each provider is resolved already, so drop its parent
                   "providers": {n: {k: v for k, v in p.items() if k != "extends"} for n, p in cfg["providers"].items()}}
        cfg["defaults"].update(backend="tmux", tmux_socket=self.socket, workspace=None,
                               max_workers=self.a.max_workers, max_wall_minutes=self.a.budget_minutes)
        return cfg

    def env(self) -> dict:
        env = clean_env(os.environ)
        env["TMPDIR"] = str(self.tmp)
        if self.name == "fleet":
            env["FLEET_CONFIG"] = str(self.config)
        if self.a.fake:
            env.update(HOME=str(self.root / "home"), PATH=f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                       FAKE_BENCH_PLAN=str(self.root / "fake-plan.json"))
        return env

    def command(self) -> list[str]:
        if self.name == "single":
            return agent_command(self.case, single_prompt(self.case))
        return agent_command(self.case, lead_prompt(self.case, str(self.skill), self.a),
                             lead_rules(str(self.skill), SKILL_PATHS[self.cli]))

    def start_tmux(self, env: dict) -> None:
        """The private server, started here rather than by the lead, so workers' windows are children of a server
        outside any sandbox the lead's CLI runs its commands in, with this arm's environment."""
        res = subprocess.run(["tmux", "-L", self.socket, "new-session", "-d", "-s", "bench-hold", "sleep 86400"],
                             env=env, capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise BenchError(f"could not start tmux -L {self.socket}: {res.stderr.strip()}")
        self.tmux_started = True
        self.say(f"private tmux server: tmux -L {self.socket}")

    # run

    def run(self) -> dict:
        result = {"arm": self.name, "commit": None, "exit": None, "timed_out": False, "wall_s": None}
        result["commit"] = self.setup()
        env = self.env()
        if self.name == "fleet":
            if not shutil.which("tmux"):
                raise BenchError("the fleet arm runs workers on a private tmux server; install tmux")
            self.start_tmux(env)
        argv = self.command()
        result["command"] = argv
        self.say(f"clone {self.repo} at {result['commit'][:12]}; agent started")
        start = time.monotonic()
        out_path = self.root / "agent.out"
        with out_path.open("w") as out, (self.root / "agent.err").open("w") as err:
            self.proc = subprocess.Popen(argv, cwd=self.repo, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                         stderr=err, start_new_session=True)
            try:
                result["exit"] = self.proc.wait(timeout=self.a.budget_minutes * 60)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                self.notes.append(f"stopped at the {self.a.budget_minutes}-minute budget")
                self.stop_agent()
        result["wall_s"] = round(time.monotonic() - start, 1)
        self.say(f"agent exited {result['exit']} after {result['wall_s']}s")
        parsed = fleet.headless_output(out_path.read_text(errors="replace"))
        agent_tokens = fleet.usage_totals(parsed["usage"]) if parsed["usage"] else None
        result["agent"] = {"tokens": agent_tokens, "message": (parsed["message"] or "")[-2000:],
                           "errors": parsed["errors"][:10], "denials": parsed["denials"][:20],
                           "stderr_tail": fleet.screen_tail((self.root / "agent.err").read_text(errors="replace"), 15)}
        segments = [{"label": "lead" if self.name == "fleet" else "agent", "model": self.case["model"]["model"],
                     "tokens": agent_tokens}]
        result["tokens"] = {"agent": agent_tokens, "workers": None, "workers_unknown": None, "workers_count": None}
        result["review_findings"], result["review_note"] = None, "no review: one agent"
        if self.name == "fleet":
            runs = self.fleet_runs()
            result["fleet_runs"] = runs
            costs = [self.fleet_cost(r) for r in runs]
            entries = [e for c in costs if c for e in c.get("ledger", [])]
            result["tokens"]["workers_count"] = len(entries)
            result["tokens"]["workers_unknown"] = sum(not e.get("tokens") for e in entries)
            for c in costs:
                if c:
                    result["tokens"]["workers"] = add_tokens(result["tokens"]["workers"], c["totals"]["tokens"])
            result["worker_wall_s"] = round(sum(c["totals"]["wall_s"] for c in costs if c), 1) if costs else None
            segments += [{"label": f"worker {e['worker']}", "model": e.get("model"), "tokens": e.get("tokens")}
                         for e in entries]
            result["review_findings"], result["review_note"] = self.review_findings(runs)
            if not runs:
                self.notes.append(f"the lead started no Fleet run")
        result["cost"] = cost(segments, self.prices)
        result["checks"] = self.run_checks()
        passed = {c["id"]: c["passed"] for c in result["checks"]}
        result["criteria"] = [{"id": c["id"], "text": c["text"], "checks": c.get("checks", []),
                               "passed": all(passed[x] for x in c["checks"]) if c.get("checks") else None,
                               "by": "checks" if c.get("checks") else "owner"} for c in self.case["criteria"]]
        result["diffstat"] = git("diff", "--stat", "HEAD", cwd=self.repo)[-2000:]
        result["untracked"] = git("ls-files", "--others", "--exclude-standard", cwd=self.repo).splitlines()[:50]
        result["manual_fixes"] = None
        result["notes"] = self.notes
        return result

    def stop_agent(self) -> None:
        if self.proc and self.proc.poll() is None:
            for sig, wait in ((signal.SIGINT, 10), (signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                try:
                    os.killpg(self.proc.pid, sig)
                except ProcessLookupError:
                    return
                try:
                    self.proc.wait(timeout=wait)
                    return
                except subprocess.TimeoutExpired:
                    continue

    def fleet_cmd(self, *args: str, timeout: int = 120) -> tuple[int | None, str]:
        try:
            res = subprocess.run([sys.executable, str(self.skill / "bin" / "fleet"), *args], cwd=self.repo,
                                 env=self.env(), capture_output=True, text=True, timeout=timeout,
                                 stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return None, f"fleet {' '.join(args)} did not finish in {timeout}s"
        return res.returncode, res.stdout + res.stderr

    def fleet_runs(self) -> list[str]:
        base = self.repo / ".fleet" / "runs"
        return sorted(p.parent.name for p in base.glob("*/run.json")) if base.is_dir() else []

    def fleet_cost(self, run: str) -> dict | None:
        code, out = self.fleet_cmd("cost", run, "--json")
        try:
            return json.loads(out) if code == 0 else None
        except ValueError:
            self.notes.append(f"fleet cost {run} --json printed no JSON")
            return None

    def review_findings(self, runs: list[str]) -> tuple[int | None, str]:
        """Findings in the reviewers' report headers. Unknown when a review report has no readable header."""
        total, reviews, unreadable = 0, 0, 0
        for run in runs:
            d = self.repo / ".fleet" / "runs" / run
            try:
                workers = json.loads((d / "run.json").read_text()).get("workers", {})
            except (OSError, ValueError):
                continue
            for wid, w in workers.items():
                if w.get("role") != "review":
                    continue
                reviews += 1
                try:
                    header, problems = fleet.parse_header((d / "reports" / f"{wid}.md").read_text())
                except OSError:
                    header = None
                if not header or not isinstance(header.get("findings"), list):
                    unreadable += 1
                else:
                    total += len(header["findings"])
        if not reviews:
            return None, "no review ran"
        if unreadable:
            return None, f"{unreadable} of {reviews} review reports have no readable header"
        return total, f"from {reviews} review report{'s' if reviews != 1 else ''}"

    def run_checks(self) -> list[dict]:
        out = []
        env = clean_env(os.environ)
        env["TMPDIR"] = str(self.tmp)
        for c in self.case["checks"]:
            start = time.monotonic()
            try:
                res = subprocess.run(c["run"], shell=True, cwd=self.repo, env=env, capture_output=True, text=True,
                                     timeout=c.get("timeout", CHECK_TIMEOUT_S), stdin=subprocess.DEVNULL)
                code, tail = res.returncode, fleet.screen_tail(res.stdout + res.stderr, 15)
            except subprocess.TimeoutExpired:
                code, tail = None, f"timed out after {c.get('timeout', CHECK_TIMEOUT_S)}s"
            out.append({"id": c["id"], "run": c["run"], "passed": code == 0, "exit": code,
                        "seconds": round(time.monotonic() - start, 1), "tail": tail})
            self.say(f"check {c['id']}: {'pass' if code == 0 else 'FAIL'}")
        return out

    def cleanup(self) -> None:
        """Stop the agent, every Fleet worker and the private tmux server, then delete the clone unless --keep."""
        self.stop_agent()
        if self.name == "fleet" and (self.skill / "bin" / "fleet").is_file():
            for run in self.fleet_runs():
                self.fleet_cmd("stop", run, "--all", "--timeout", "10", timeout=300)
        if self.tmux_started or self.name == "fleet":
            subprocess.run(["tmux", "-L", self.socket, "kill-server"], capture_output=True, timeout=30)
        if self.a.keep:
            self.say(f"kept {self.root}")
        else:
            shutil.rmtree(self.root, ignore_errors=True)


# ---------- results ----------

def kit_info(case: dict, a) -> dict:
    try:
        kit_commit = git("rev-parse", "HEAD", cwd=ROOT).strip()
    except BenchError:
        kit_commit = None
    cli = case["model"]["cli"]
    version = None
    if not a.fake and shutil.which(cli):
        try:
            res = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20)
            version = (res.stdout + res.stderr).strip().splitlines()[0][:120] if res.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, IndexError):
            version = None
    return {"fleet_version": fleet.VERSION, "kit_commit": kit_commit, "cli_version": version}


def result_path(results: Path, case: dict, date: str, fake: bool) -> Path:
    return results / f"{case['name']}-{date}{'-fake' if fake else ''}.json"


def save_result(results: Path, case: dict, a, arms: dict, date: str) -> Path:
    """Write (or add these arms to) the day's result file for this case."""
    results.mkdir(parents=True, exist_ok=True)
    path = result_path(results, case, date, a.fake)
    data = json.loads(path.read_text()) if path.exists() else {"schema": 1, "arms": {}}
    data.update(case=case["name"], size=case["size"], date=date, fake=bool(a.fake), repo=case["repo"],
                commit=case["commit"], model=case["model"], ticket=case["ticket"], kit=kit_info(case, a),
                budget={"minutes": a.budget_minutes, "max_workers": a.max_workers})
    data["arms"].update(arms)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# ---------- the table ----------

UNKNOWN = "unknown"


def minutes(seconds) -> str:
    if seconds is None:
        return UNKNOWN
    return f"{seconds:.0f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def criteria_cell(arm: dict) -> tuple[str, float | None]:
    crit = arm.get("criteria") or []
    if not crit:
        return UNKNOWN, None
    yes = sum(c.get("passed") is True for c in crit)
    open_ = sum(c.get("passed") is None for c in crit)
    text = f"{yes} of {len(crit)}" + (f" ({open_} for the owner to mark)" if open_ else "")
    return text, (yes if not open_ else None)


def checks_cell(arm: dict) -> tuple[str, int | None]:
    checks = arm.get("checks")
    if not checks:
        return UNKNOWN, None
    yes = sum(c.get("passed") is True for c in checks)
    return f"{yes} of {len(checks)}", yes


def findings_cell(arm: dict) -> str:
    n = arm.get("review_findings")
    note = arm.get("review_note") or ""
    if n is None:
        return f"none ({note})" if note.startswith("no review") else f"{UNKNOWN} ({note})" if note else UNKNOWN
    return f"{n} ({note})" if note else str(n)


def cost_cell(arm: dict) -> tuple[str, float | None]:
    c = arm.get("cost") or {}
    if c.get("usd") is None:
        why = "; ".join(c.get("missing") or []) or "not priced"
        return f"{UNKNOWN} ({why})", None
    if c.get("complete"):
        return f"~${c['usd']:.2f} (estimate)", c["usd"]
    return f"at least ~${c['usd']:.2f} (estimate; not priced: {'; '.join(c['missing'])})", None


def fixes_cell(arm: dict) -> tuple[str, int | None]:
    f = arm.get("manual_fixes")
    if f is None:
        return "not filled in yet (owner)", None
    n = len(f) if isinstance(f, list) else int(f)
    return str(n), n


def wall_cell(arm: dict) -> tuple[str, float | None]:
    w = arm.get("wall_s")
    text = minutes(w)
    if arm.get("timed_out"):
        text += " (stopped at the budget)"
    if arm.get("arm") == "fleet" and arm.get("worker_wall_s") is not None:
        text += f"; workers {minutes(arm['worker_wall_s'])} summed"
    return text, w


def comparison(fleet_arm: dict | None, single: dict | None) -> list[str]:
    """Where Fleet won, lost or tied against one agent; a metric either side does not know is not compared."""
    if not fleet_arm or not single:
        return ["Not compared: only one arm has run."]
    rows = []
    metrics = (("criteria passed", criteria_cell, True), ("checks passed", checks_cell, True),
               ("wall time", wall_cell, False), ("cost", cost_cell, False), ("manual fixes", fixes_cell, False))
    for name, cell, more_is_better in metrics:
        (ft, fv), (st, sv) = cell(fleet_arm), cell(single)
        if fv is None or sv is None:
            rows.append(f"{name}: not compared (unknown or not filled in for {'both' if fv is None and sv is None else 'Fleet' if fv is None else 'one agent'})")
        elif fv == sv:
            rows.append(f"{name}: tie")
        else:
            won = (fv > sv) == more_is_better
            rows.append(f"{name}: **Fleet {'won' if won else 'lost'}** ({ft} against {st})")
    return rows


def render_table(results: Path) -> str:
    files = sorted(results.glob("*.json")) if results.is_dir() else []
    out = ["# Benchmark results", "",
           "Generated by `python3 benchmarks/run.py`; do not edit by hand. Edit `manual_fixes` and owner-marked "
           "criteria in the JSON files, then run `python3 benchmarks/run.py --table`. See "
           "[the method](../README.md). Unknown values are shown as unknown, never as zero, and every loss is shown.",
           ""]
    if not files:
        out += ["No results yet. Fleet makes no speed, cost or quality claim until results exist."]
        return "\n".join(out) + "\n"
    for f in files:
        try:
            data = json.loads(f.read_text())
        except ValueError:
            out += [f"## {f.name}", "", "Unreadable result file.", ""]
            continue
        m = data.get("model", {})
        title = f"## {data.get('case', f.stem)} ({data.get('size', '?')}), {data.get('date', '?')}"
        out += [title + (" — fake run: plumbing only, not a benchmark" if data.get("fake") else ""), "",
                f"Model `{m.get('model')}` at effort `{m.get('effort')}` on {m.get('cli')}, both arms. Commit "
                f"`{str(data.get('commit'))[:12]}` of {data.get('repo')}. Fleet {data.get('kit', {}).get('fleet_version')}.",
                "", "| Arm | Criteria passed | Checks passed | Review findings | Wall time | Cost | Manual fixes |",
                "| --- | --- | --- | --- | --- | --- | --- |"]
        arms = data.get("arms", {})
        for name, label in (("fleet", "Fleet"), ("single", "One agent")):
            arm = arms.get(name)
            if not arm:
                out.append(f"| {label} | not run | not run | not run | not run | not run | not run |")
                continue
            cells = [criteria_cell(arm)[0], checks_cell(arm)[0], findings_cell(arm), wall_cell(arm)[0],
                     cost_cell(arm)[0], fixes_cell(arm)[0]]
            out.append(f"| {label} | " + " | ".join(c.replace("|", "/") for c in cells) + " |")
        out += ["", "Fleet against one agent:", ""]
        out += [f"- {r}" for r in comparison(arms.get("fleet"), arms.get("single"))]
        out += [""]
    return "\n".join(out).rstrip("\n") + "\n"


def write_table(results: Path) -> Path:
    results.mkdir(parents=True, exist_ok=True)
    path = results / "README.md"
    path.write_text(render_table(results))
    return path


# ---------- plan and main ----------

def arm_names(arm: str) -> list[str]:
    return list(ARMS) if arm == "both" else [arm]


def print_plan(case: dict, a) -> None:
    m = case["model"]
    print(f"benchmark {case['name']} ({case['size']}): {m['cli']} {m['model']} at effort {m['effort']}, "
          f"{case['repo']} at {case['commit']}")
    print(f"Budget: {a.budget_minutes} minutes per arm (the agent's wall clock; for Fleet also max_wall_minutes, "
          f"summed worker time), max_workers {a.max_workers}.")
    print(f"Costs: {'from ' + a.prices if a.prices else 'unknown: no --prices file'}.")
    for name in arm_names(a.arm):
        print(f"\n{name} arm: clone into a fresh temporary directory <tmp>, then in <tmp>/repo run:")
        if name == "fleet":
            skill = f"<tmp>/repo/{SKILL_PATHS[m['cli']]}"
            print(f"  node {ROOT / 'bin' / 'fleet.js'} setup --harness {m['cli']} --scope project --root <tmp>/repo")
            print(f"  tmux -L fleet-bench-<id> new-session -d -s bench-hold 'sleep 86400'   # its private server")
            print(f"  FLEET_CONFIG=<tmp>/config.toml TMPDIR=<tmp>/tmp \\")
            argv = agent_command(case, lead_prompt(case, skill, a), lead_rules(skill, SKILL_PATHS[m["cli"]]))
        else:
            print(f"  TMPDIR=<tmp>/tmp \\")
            argv = agent_command(case, single_prompt(case))
        print("    " + shlex.join(argv[:-1]) + " <prompt below>")
        print("  prompt:\n" + "\n".join("    | " + line for line in argv[-1].splitlines()))
        if name == "fleet":
            print("  then: fleet cost <run> --json for the workers, fleet stop <run> --all, tmux kill-server")
        print("  then the case's checks: " + "; ".join(c["run"] for c in case["checks"]))
        skipped = [c["id"] for c in case["checks"] if "," in c["run"]]
        if m["cli"] == "claude" and skipped:
            print(f"  note: checks {', '.join(skipped)} hold a comma, so they get no allow rule; add one to `allow`")
    if "fleet" in arm_names(a.arm) and not a.fake:
        try:
            cfg = fleet.load_config(fleet.USER_CONFIG)
            on = [n for n, p in cfg["providers"].items() if p.get("enabled", True) and not p.get("placeholder")]
            heavy = {n: fleet.resolve_model(cfg["providers"][n], "heavy", None, None) for n in on}
            print("\nFleet workers use your configured providers: " +
                  ", ".join(f"{n} heavy {h[0]}" + (f"@{h[1]}" if h[1] else "") for n, h in heavy.items()))
            same = [n for n, h in heavy.items() if h[0] == m["model"]]
            if not same:
                print(f"  note: no provider's heavy tier runs {m['model']}; for the same top model, set it in your "
                      "config before the run (see benchmarks/README.md)")
        except Exception as e:  # the plan still prints; a broken config shows up again at run time
            print(f"\n(could not read your Fleet config: {e})")
    print("\nNothing was run or written. Your checkout, Fleet config and tmux server are never written.")
    print(f"To run it: python3 {shlex.quote(str(Path(__file__)))} {shlex.quote(a.case)} --arm {a.arm} --yes-paid "
          f"--budget-minutes {a.budget_minutes} --max-workers {a.max_workers}"
          + (f" --prices {shlex.quote(a.prices)}" if a.prices else "") + (" --keep" if a.keep else ""))


def preflight(case: dict, a) -> None:
    for tool in ("git", *([] if a.fake else [case["model"]["cli"]])):
        if not shutil.which(tool):
            raise BenchError(f"{tool} is not on PATH")
    if "fleet" in arm_names(a.arm):
        for tool in ("tmux", "node"):
            if not shutil.which(tool):
                raise BenchError(f"the fleet arm needs {tool}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="benchmarks/run.py", description=__doc__.splitlines()[0])
    ap.add_argument("case", nargs="?", help="a case name under benchmarks/cases/, its directory or its case.json")
    ap.add_argument("--arm", choices=["fleet", "single", "both"], default="both")
    ap.add_argument("--yes-paid", action="store_true", help="run the real CLIs; this spends real usage")
    ap.add_argument("--fake", action="store_true", help="run both arms against the fake CLIs (CI); nothing paid")
    ap.add_argument("--budget-minutes", type=int, default=60,
                    help="per arm: the agent's wall clock and Fleet's max_wall_minutes (default 60)")
    ap.add_argument("--max-workers", type=int, default=6, help="Fleet's max_workers (default 6)")
    ap.add_argument("--prices", help="the owner's price file (TOML, USD per million tokens); none is built in")
    ap.add_argument("--keep", action="store_true", help="keep each arm's temporary directory")
    ap.add_argument("--results", help="where result files and the table go (default benchmarks/results/; with "
                                      "--fake, a new temporary directory, so fake rows never land in the repo)")
    ap.add_argument("--table", action="store_true", help="only rebuild <results>/README.md")
    a = ap.parse_args(argv)
    if a.results:
        results = Path(a.results).resolve()
    elif a.fake and not a.table:
        results = Path(tempfile.mkdtemp(prefix="fleet-bench-results-")).resolve()
    else:
        results = KIT / "results"
    try:
        if a.table:
            print(f"table: {write_table(results)}")
            return 0
        if not a.case:
            ap.error("a case is required (or --table)")
        if not 1 <= a.budget_minutes <= 24 * 60:
            raise BenchError("--budget-minutes must be between 1 and 1440")
        if not 1 <= a.max_workers <= 100:
            raise BenchError("--max-workers must be between 1 and 100")
        case = load_case(a.case)
        prices = load_prices(a.prices)
        if not a.yes_paid and not a.fake:
            print_plan(case, a)
            return 0
        preflight(case, a)
    except BenchError as e:
        print(f"benchmarks/run.py: {e}", file=sys.stderr)
        return 1
    return run_arms(case, a, prices, results)


def run_arms(case: dict, a, prices: dict | None, results: Path) -> int:
    date = datetime.date.today().isoformat()
    done, arm, code = {}, None, 0

    def on_term(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, on_term)
    try:
        for name in arm_names(a.arm):
            arm = Arm(name, case, a, prices)
            try:
                done[name] = arm.run()
            except (BenchError, OSError, subprocess.SubprocessError) as e:
                print(f"[{name}] failed: {e}", file=sys.stderr, flush=True)
                code = 1
            finally:
                arm, current = None, arm
                current.cleanup()
    except KeyboardInterrupt:
        if arm is not None:  # interrupted while creating the arm, before its own cleanup could run
            arm.cleanup()
        print("interrupted: cleaned up; no result written", file=sys.stderr, flush=True)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous)
    if done:
        commits = {r["commit"] for r in done.values()}
        if len(commits) > 1:
            print(f"warning: the arms ran on different commits {sorted(commits)}", file=sys.stderr)
        path = save_result(results, case, a, done, date)
        print(f"result: {path}")
        print(f"table: {write_table(results)}")
    return code


if __name__ == "__main__":
    sys.exit(main())
