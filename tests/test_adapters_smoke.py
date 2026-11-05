"""Every shipped adapter's launch line, end to end through fleet in headless tmux, against a strict fake CLI.

For each adapter in skill/providers.toml that is not a placeholder, a fake executable named after its `bin` goes on
the worker's PATH (tests/fixtures/fake_cli.py). It accepts only the exact command line the adapter builds, finds
the Fleet prompt file, writes a needs-verification report and quits on the adapter's own quit sequence. Each test
runs launch -> wait (REPORT) -> stop (confirmed exit) with a config that turns that one adapter on.

These prove the launch line's shape, the quit sequence and account isolation, not that a real CLI or account
works: no real CLI, account or network is used.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from fleet_harness import FIXTURES, FLEET, HAS_TMUX, FleetCase  # noqa: E402

loader = importlib.machinery.SourceFileLoader("fleet_adapter_smoke", str(FLEET))
spec = importlib.util.spec_from_loader(loader.name, loader)
fleet = importlib.util.module_from_spec(spec)
loader.exec_module(fleet)

FAKE_CLI = FIXTURES / "fake_cli.py"
SHIPPED = fleet.load_config(Path("/nonexistent/fleet-config.toml"))["providers"]
SMOKED = ("claude", "claude-co", "codex", "cursor", "agy", "opencode")
LEAD_ACCOUNTS = {"CLAUDE_CONFIG_DIR": "/nonexistent/lead-claude-account", "CODEX_HOME": "/nonexistent/lead-codex-home"}


def install_fakes(bindir: Path) -> None:
    """One executable per shipped binary name, each running the strict fake as that CLI."""
    bindir.mkdir()
    for name in {p["bin"] for p in SHIPPED.values() if not p.get("placeholder")}:
        path = bindir / name
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" {name} "$@"\n')
        path.chmod(0o755)


class ShippedAdapters(unittest.TestCase):
    def test_every_supported_adapter_is_smoked(self):
        supported = {n for n, p in SHIPPED.items() if not p.get("placeholder")}
        self.assertEqual(supported, set(SMOKED), "add a smoke test for each new adapter (and a grammar in fake_cli.py)")

    def test_the_fake_accepts_only_the_adapters_own_line(self):
        """The fake is strict: one stray flag, a wrong model or a missing prompt fails it loudly."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "run" / "prompts" / "01.md"
            prompt.parent.mkdir(parents=True)
            (root / "run" / "reports").mkdir()
            prompt.write_text("# prompt\n")
            install_fakes(root / "bin")
            env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}", FAKE_CLI_LOG=str(root),
                       FAKE_CLI_EXPECT=str(root / "expect.json"))
            for var in LEAD_ACCOUNTS:
                env.pop(var, None)
            line = fleet.worker_prompt_line(prompt, "01")
            for name in SMOKED:
                p = SHIPPED[name]
                model, effort = fleet.resolve_model(p, "standard", None, None)
                (root / "expect.json").write_text(json.dumps({
                    "model": model, "effort": effort, "session": "new" if fleet.resumable(p) else None,
                    "account_dir": fleet.account_env(p)[0].get(p.get("account_env") or "")}))
                good = fleet.build_launch(p, model, effort, line, "0" * 8 + "-0000-4000-8000-" + "0" * 12)
                bad = {"an unknown flag": good.replace(f"{p['bin']} ", f"{p['bin']} --yolo ", 1),
                       "another model": fleet.build_launch(p, "not-" + model, effort, line,
                                                           "0" * 8 + "-0000-4000-8000-" + "0" * 12),
                       "no Fleet prompt": fleet.build_launch(p, model, effort, "do the thing")}
                with self.subTest(name):
                    quit_word = p["quit"][0]
                    res = subprocess.run(good, shell=True, env=env, input=f"hello\n{quit_word}\n", text=True,
                                         capture_output=True, timeout=30)
                    self.assertEqual(res.returncode, 0, f"{good}\n{res.stdout}{res.stderr}")
                    self.assertIn(f"fake {p['bin']} quits", res.stdout)
                    self.assertIn("Status: needs-verification", (root / "run" / "reports" / "01.md").read_text())
                    for why, command in bad.items():
                        res = subprocess.run(command, shell=True, env=env, input="", text=True,
                                             capture_output=True, timeout=30)
                        self.assertEqual(res.returncode, 2, f"{why}: {command}\n{res.stdout}{res.stderr}")
                        self.assertIn(f"FAKE {p['bin']}: ", res.stdout, why)


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class AdapterSmoke(FleetCase):
    """launch -> wait -> stop for one shipped adapter, through fleet in headless tmux."""

    def setUp(self):
        super().setUp()
        self.bindir = self.root / "bin"
        install_fakes(self.bindir)
        self.log = self.root / "fake-log"
        self.log.mkdir()
        self.expect = self.root / "expect.json"
        self.env.update(PATH=f"{self.bindir}:{self.env['PATH']}", FAKE_CLI_LOG=str(self.log),
                        FAKE_CLI_EXPECT=str(self.expect), **LEAD_ACCOUNTS)  # the lead's accounts must not leak

    def expectations(self, name: str, **extra) -> dict:
        p = SHIPPED[name]
        model, effort = fleet.resolve_model(p, "standard", None, None)
        want = {"model": model, "effort": effort, "session": "new" if fleet.resumable(p) else None,
                # An adapter's own account dir (expanded under this test's HOME), else nothing inherited.
                "account_dir": (str(Path(p["env"][p["account_env"]].replace("~", str(self.home), 1)))
                                if p.get("account_env") in p.get("env", {}) else None)}
        want.update(extra)
        self.expect.write_text(json.dumps(want))
        return want

    def diagnostics(self) -> str:
        logged = "".join(f.read_text() for f in sorted(self.log.glob("*.errors")))
        return super().diagnostics() + (f"\n--- the fake CLI refused:\n{logged}" if logged else "")

    def errors(self, name: str) -> str:
        f = self.log / f"{SHIPPED[name]['bin']}.errors"
        return f.read_text() if f.exists() else ""

    def calls(self, name: str) -> list[dict]:
        f = self.log / f"{SHIPPED[name]['bin']}.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def worker(self) -> dict:
        return json.loads((self.repo / ".fleet/runs/smoke/run.json").read_text())["workers"]["01"]

    def launch_report_stop(self, name: str, extra_config: str = "", **extra) -> dict:
        want = self.expectations(name, **extra)
        self.write_config(f'review = "off"\nprefer = ["{name}"]\n{extra_config}\n[providers.{name}]\nenabled = true\n')
        self.fleet("init", "smoke")
        (self.repo / "01.md").write_text("# Smoke\nChange a.txt.\n")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": "01", "ticket": "01.md", "files": ["a.txt"], "provider": name, "tier": "standard",
             "context": "A smoke test.", "checks": "manual prose review is sufficient"}]}))
        self.fleet("plan", "smoke", "--file", str(self.repo / "plan.json"))
        effort = f"@{want['effort']}" if want["effort"] else ""
        self.assertRegex(self.fleet("launch", "smoke", "--ready"),
                         rf"LAUNCHED 01 {re.escape(name)}:{re.escape(want['model'])}{re.escape(effort)} standard @\d+")
        self.report_then_stop(name)
        return want

    def report_then_stop(self, name: str) -> None:
        out = self.wait_for("smoke", r"^REPORT 01\b")
        self.assertIn("needs-verification", out)
        self.assertEqual(self.errors(name), "")
        self.assertRegex(self.fleet("stop", "smoke", "01", "--timeout", "10"), r"01: exited \(@\d+\)")
        w = self.worker()
        self.assertEqual((w["state"], w.get("returncode")), ("exited", 0), self.diagnostics())
        self.assertIn(f"fake {SHIPPED[name]['bin']} quits", self.fleet("peek", "smoke", "01"))

    def test_claude(self):
        self.launch_report_stop("claude")
        [call] = self.calls("claude")
        self.assertEqual(call["argv"][:2], ["--session-id", self.worker()["session"]])

    def test_claude_co(self):
        want = self.launch_report_stop("claude-co")
        self.assertEqual(want["account_dir"], str(self.home / ".claude-co"))
        self.assertEqual(len(self.calls("claude-co")), 1)

    def test_codex(self):
        self.launch_report_stop("codex")
        self.assertEqual(len(self.calls("codex")), 1)

    def test_codex_with_auto_trust(self):
        """auto_trust adds Codex's session-only trust override, a third -c."""
        self.launch_report_stop("codex", "auto_trust = true", trust_repo=str(self.repo))
        self.assertIn(f"projects={{{json.dumps(str(self.repo))}={{trust_level=\"trusted\"}}}}", self.calls("codex")[0]["argv"])

    def test_cursor(self):
        self.launch_report_stop("cursor")
        self.assertIn("--trust", self.calls("cursor")[0]["argv"])

    def test_agy(self):
        self.launch_report_stop("agy")
        self.assertEqual(len(self.calls("agy")), 1)

    def test_opencode(self):
        self.launch_report_stop("opencode")
        self.assertEqual(self.calls("opencode")[0]["argv"][2], "--prompt")

    def test_claude_resume(self):
        """launch --resume continues the recorded session with the adapter's resume fragment."""
        self.launch_report_stop("claude")
        session = self.worker()["session"]
        self.expectations("claude", session="resume", session_id=session)
        self.fleet("launch", "smoke", "01", "claude", "--tier", "standard", "--resume", "--settle", "0")
        self.report_then_stop("claude")
        first, again = self.calls("claude")
        self.assertEqual((first["resume"], again["resume"]), (False, True))
        self.assertEqual(again["argv"][:2], ["--resume", session])
        self.assertEqual(self.worker()["session"], session)


if __name__ == "__main__":
    unittest.main()
