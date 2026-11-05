"""fleet smoke, the owner's live check, run against strict fake CLIs (tests/fixtures/fake_cli.py) on PATH.

The "owner" here is the test: its repository (FleetCase's repo), its FLEET_CONFIG and its tmux socket are the
sentinels the smoke must leave untouched, while the smoke builds its own throwaway repository, config and private
tmux server under the test's TMPDIR. HOME is the test's own, so no real CLI, account or network is used.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from fleet_harness import FIXTURES, FLEET, HAS_TMUX, FleetCase  # noqa: E402

loader = importlib.machinery.SourceFileLoader("fleet_smoke_kit", str(FLEET))
spec = importlib.util.spec_from_loader(loader.name, loader)
fleet = importlib.util.module_from_spec(spec)
loader.exec_module(fleet)

SHIPPED = fleet.load_config(Path("/nonexistent/fleet-config.toml"))
OWNER_CONFIG = """
[providers.claude]
login = "echo Logged in"
login_ok = "Logged in"

[providers.codex]
login = "echo Logged in"
login_ok = "Logged in"

[providers.claude-co]
enabled = true

[providers.cursor]
enabled = false
"""


def expectation(name: str) -> dict:
    """What the fake accepts from this adapter: its heavy tier, which is also its review tier."""
    p = SHIPPED["providers"][name]
    heavy, review = fleet.resolve_model(p, "heavy", None, None), fleet.resolve_model(p, "review", None, None)
    assert heavy == review, f"{name}: the smoke tests assume one model for writer and reviewer"
    modes = {profile_mode(p, role) for role in ("worker", "review")}  # one fake serves the writer and the reviewer
    return {"model": heavy[0], "effort": heavy[1], "session": "new" if fleet.resumable(p) else None, "account_dir": None,
            **({"permission_modes": sorted(modes, key=str)} if modes != {None} else {})}


def profile_mode(p: dict, role: str) -> str | None:
    """The --permission-mode a shipped profile passes, or None when it passes none; hooks never change it."""
    args = p.get("profiles", {}).get(role, {}).get("args", "").split()
    return args[args.index("--permission-mode") + 1] if "--permission-mode" in args else None


class SmokeConfig(unittest.TestCase):
    def test_the_smoke_config_round_trips_and_keeps_only_its_providers(self):
        cfg = fleet.smoke_config(SHIPPED, ["claude", "codex"], "fleet-smoke-test")
        loaded = tomllib.loads(fleet.toml_text(cfg))
        self.assertEqual(loaded["defaults"]["tmux_socket"], "fleet-smoke-test")
        self.assertEqual((loaded["defaults"]["backend"], loaded["defaults"]["review"]), ("tmux", "cross-heavy"))
        self.assertNotIn("workspace", loaded["defaults"])
        on = {n for n, p in loaded["providers"].items() if p.get("enabled")}
        self.assertEqual(on, {"claude", "codex"})
        self.assertEqual(loaded["providers"]["codex"]["tiers"], SHIPPED["providers"]["codex"]["tiers"])

    def test_routes_pick_a_reviewer_from_another_family_or_none(self):
        cfg = fleet.smoke_config(SHIPPED, ["claude", "codex"], "s")
        routes = fleet.smoke_routes(cfg, ["claude", "codex"])
        self.assertEqual((routes["claude"]["reviewer"], routes["codex"]["reviewer"]), ("codex", "claude"))
        self.assertEqual(routes["claude"]["tier"], "heavy")
        alone = fleet.smoke_routes(fleet.smoke_config(SHIPPED, ["claude"], "s"), ["claude"])
        self.assertEqual((alone["claude"]["reviewer"], alone["claude"]["tier"]), (None, "standard"))


class SmokeCase(FleetCase):
    """Fakes for claude and codex on PATH, the owner's sentinel config, and a TMPDIR the smoke works under."""

    def setUp(self):
        super().setUp()
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        for name in ("claude", "codex"):
            path = self.bindir / name
            path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FIXTURES / "fake_cli.py"}" {name} "$@"\n')
            path.chmod(0o755)
        self.log = self.root / "fake-log"
        self.log.mkdir()
        expect = self.root / "expect.json"
        expect.write_text(json.dumps({"by_cli": {n: expectation(n) for n in ("claude", "codex")}}))
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        self.env.update(PATH=f"{self.bindir}:{self.env['PATH']}", FAKE_CLI_LOG=str(self.log),
                        FAKE_CLI_EXPECT=str(expect), TMPDIR=str(self.tmp))
        self.write_config(OWNER_CONFIG)
        self.before = self.owner_state()

    def owner_state(self) -> dict:
        """Everything of the owner's the smoke must not write: the config's bytes and the repository's files."""
        files = {str(p.relative_to(self.repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(self.repo.rglob("*")) if p.is_file()}
        return {"config": self.config.read_bytes(), "files": files, "status": self.git("status", "--porcelain")}

    def assert_owner_untouched(self) -> None:
        self.assertEqual(self.owner_state(), self.before)
        self.assertFalse((self.repo / ".fleet").exists())
        if HAS_TMUX:
            owner_tmux = subprocess.run(["tmux", "-L", self.socket, "list-sessions"], capture_output=True)
            self.assertNotEqual(owner_tmux.returncode, 0, "the smoke started the owner's tmux server")

    def smoke_socket(self, out: str) -> str:
        return re.search(r"tmux -L (fleet-smoke-[0-9a-f]+)", out).group(1)

    def assert_cleaned(self, out: str) -> None:
        gone = subprocess.run(["tmux", "-L", self.smoke_socket(out), "list-sessions"], capture_output=True)
        self.assertNotEqual(gone.returncode, 0, "the smoke's tmux server outlived it")
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.is_dir()], [], "the throwaway repository is left")


class SmokePlanOnly(SmokeCase):
    def test_without_yes_paid_it_prints_the_plan_and_writes_nothing(self):
        out = self.fleet("smoke")
        self.assertIn("fleet smoke would run 2 providers on your real CLIs and accounts", out)
        self.assertRegex(out, r"claude +2 sessions: writer opus@high \(heavy, family anthropic\), reviewed by codex")
        self.assertRegex(out, r"codex +2 sessions: writer gpt-6.1-sol@high \(heavy, family openai\), reviewed by claude")
        self.assertNotIn("claude-co", out.split("To run it")[0])  # one account per CLI unless named
        self.assertIn("fleet smoke --providers claude,codex --yes-paid --timeout 300", out)
        self.assertEqual(list(self.tmp.iterdir()), [])
        self.assertFalse((self.log / "claude.jsonl").exists())
        self.assert_owner_untouched()

    def test_a_named_provider_must_exist(self):
        self.assertIn("unknown provider 'nope'", self.fleet("smoke", "--providers", "claude,nope", expected=1))


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class LiveSmoke(SmokeCase):
    def test_two_families_pass_every_step_including_review(self):
        report = self.root / "smoke-report.md"
        out = self.fleet("smoke", "--providers", "claude,codex", "--yes-paid", "--timeout", "30",
                         "--report", str(report), timeout=120)
        text = report.read_text()
        for step in ("setup", "init", "plan", "launch --ready", "writer claude hands back", "writer codex hands back",
                     "review claude", "reviewer review-claude hands back", "accept claude", "review codex",
                     "reviewer review-codex hands back", "accept codex", "check", "cleanup"):
            self.assertRegex(text, rf"\| {re.escape(step)} \| pass \| \d+\.\d \|", f"{step}:\n{text}")
        self.assertIn("- Result: PASS (14 passed, 0 failed, 0 skipped)", text)
        self.assertIn("| claude | pass | pass | pass |", text)
        self.assertIn("| claude | unknown (`claude --version` exited 2) |", text)  # the fake has no --version
        self.assertIn(f"report: {report}", out)
        self.assertEqual(len((self.log / "codex.jsonl").read_text().splitlines()), 2)  # a writer and a reviewer
        self.assert_cleaned(out)
        self.assert_owner_untouched()

    def test_a_stalled_worker_fails_with_its_screen_and_keep_keeps_the_repo(self):
        self.env["FAKE_CLI_STALL"] = "codex"
        out = self.fleet("smoke", "--providers", "claude,codex", "--yes-paid", "--timeout", "3", "--keep",
                         expected=1, timeout=120)
        text = Path(re.search(r"^report: (.+)$", out, re.M).group(1)).read_text()
        self.assertIn("| writer codex hands back | fail |", text)
        self.assertIn("### writer codex hands back", text)
        self.assertIn("no hand-back in 3s", text)
        self.assertIn("```text\nfake codex is waiting at a sign-in prompt\n```", text)  # its screen, in the report
        self.assertIn("| review codex | skip |", text)
        self.assertIn("| review claude | pass |", text)  # the other ticket goes on, to its (codex) reviewer
        self.assertIn("| reviewer review-claude hands back | fail |", text)
        self.assertIn("| check | pass |", text)
        kept = Path(re.search(r"^kept the smoke repository: (.+)$", out, re.M).group(1))
        self.assertTrue((kept / "smoke" / "claude.txt").is_file())
        gone = subprocess.run(["tmux", "-L", self.smoke_socket(out), "list-sessions"], capture_output=True)
        self.assertNotEqual(gone.returncode, 0)
        self.assert_owner_untouched()

    def test_an_interrupt_still_cleans_up(self):
        self.env["FAKE_CLI_STALL"] = "claude,codex"
        proc = subprocess.Popen([sys.executable, str(FLEET), "smoke", "--providers", "claude,codex", "--yes-paid",
                                 "--timeout", "60"], cwd=self.repo, env=self.env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.addCleanup(proc.kill)
        lines = []
        for line in proc.stdout:
            lines.append(line)
            if line.startswith("PASS launch --ready"):
                break
        self.assertTrue(lines and lines[-1].startswith("PASS launch --ready"), "".join(lines))
        socket = self.smoke_socket("".join(lines))
        self.assertEqual(subprocess.run(["tmux", "-L", socket, "list-sessions"], capture_output=True).returncode, 0)
        proc.send_signal(signal.SIGINT)
        rest, _ = proc.communicate(timeout=60)
        out = "".join(lines) + rest
        self.assertEqual(proc.returncode, 130, out)
        self.assertIn("FAIL interrupted", out)
        self.assertRegex(out, r"PASS cleanup")
        self.assertIn("| interrupted | fail |", Path(re.search(r"^report: (.+)$", out, re.M).group(1)).read_text())
        self.assert_cleaned(out)
        self.assert_owner_untouched()


if __name__ == "__main__":
    unittest.main()
