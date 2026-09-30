"""Unit tests for the pure parts of skill/bin/fleet. Run: python3 -m unittest discover tests"""
import importlib.machinery
import importlib.util
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("fleet", str(ROOT / "skill" / "bin" / "fleet"))
_spec = importlib.util.spec_from_loader("fleet", _loader)
fleet = importlib.util.module_from_spec(_spec)
_loader.exec_module(fleet)


class ProviderConfig(unittest.TestCase):
    def setUp(self):
        self.cfg = fleet.load_config(Path("/nonexistent/config.toml"))

    def test_every_enabled_provider_can_build_every_tier(self):
        for name, p in self.cfg["providers"].items():
            if not p.get("enabled", True):
                continue
            for tier in ("heavy", "standard", "light", "review"):
                with self.subTest(provider=name, tier=tier):
                    model, effort = fleet.resolve_model(p, tier, None, None)
                    cmd = fleet.build_launch(p, model, effort, "Read /x/p.md and follow it")
                    self.assertIn(model, cmd)
                    self.assertNotIn("{", cmd)

    def test_launch_lines_match_the_known_cli_flags(self):
        p = self.cfg["providers"]
        self.assertEqual(
            fleet.build_launch(p["codex"], "gpt-6-sol", "high", "go"),
            "env -u CODEX_HOME codex -m gpt-6-sol -c model_reasoning_effort=high -s workspace-write -a on-request go")
        self.assertEqual(
            fleet.build_launch(p["cursor"], "grok-4.7-high", None, "go"),
            "cursor-agent --model grok-4.7-high --trust go")
        home = str(Path("~/.claude-co").expanduser())
        self.assertEqual(fleet.build_launch(p["claude-co"], "opus", "high", "go"),
                         f"env CLAUDE_CONFIG_DIR={home} claude --model opus --effort high go")
        self.assertTrue(fleet.build_launch(p["claude"], "opus", "high", "go")
                        .startswith("env -u CLAUDE_CONFIG_DIR claude --model opus"))
        self.assertIn("-i go", fleet.build_launch(p["agy"], "gemini-3.1-pro-high", "high", "go"))

    def test_prompt_is_shell_quoted(self):
        cmd = fleet.build_launch(self.cfg["providers"]["codex"], "m", "high", "Read it; rm -rf / 'x'")
        self.assertTrue(cmd.endswith("'Read it; rm -rf / '\"'\"'x'\"'\"''"))

    def test_missing_effort_is_an_error_not_an_empty_flag(self):
        with self.assertRaises(fleet.FleetError):
            fleet.build_launch(self.cfg["providers"]["codex"], "m", None, "go")

    def test_explicit_model_overrides_tier(self):
        model, effort = fleet.resolve_model(self.cfg["providers"]["codex"], "heavy", "gpt-6-luna", None)
        self.assertEqual((model, effort), ("gpt-6-luna", "high"))

    def test_user_config_merges_over_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write('[defaults]\nmax_parallel = 3\n[providers.agy]\nenabled = false\n'
                     '[providers.codex.tiers.standard]\nmodel = "gpt-6-luna"\n')
        cfg = fleet.load_config(Path(fh.name))
        self.assertEqual(cfg["defaults"]["max_parallel"], 3)
        self.assertEqual(cfg["defaults"]["routing"], "auto")
        self.assertFalse(cfg["providers"]["agy"]["enabled"])
        self.assertEqual(cfg["providers"]["codex"]["tiers"]["standard"],
                         {"model": "gpt-6-luna", "effort": "medium"})
        self.assertEqual(cfg["providers"]["codex"]["bin"], "codex")

    def test_example_config_parses(self):
        with open(ROOT / "config.example.toml", "rb") as fh:
            tomllib.load(fh)


class Reports(unittest.TestCase):
    def test_status_line_variants(self):
        for text, want in [
            ("# R\n\nStatus: needs-verification\n", "needs-verification"),
            ("**Status:** blocked\n", "blocked"),
            ("**Status**: `needs-verification`\n", "needs-verification"),
            ("status: resolved\n", "resolved"),
            ("no status here\n", None),
        ]:
            with self.subTest(text=text):
                self.assertEqual(fleet.parse_status(text), want)


class Wait(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.reports, self.seen = root / "reports", root / ".seen"
        self.reports.mkdir()
        self.seen.mkdir()
        self.report = self.reports / "07.md"

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self):
        return [(i, s) for i, s, _ in fleet.scan_reports(self.reports, self.seen, fleet.WAKE_STATUSES)]

    def write(self, text):
        self.report.write_text(text)
        ns = self.report.stat().st_mtime_ns + 1_000_000  # distinct mtime even on fast filesystems
        os.utime(self.report, ns=(ns, ns))

    def test_verification_cycle_wakes_exactly_when_the_worker_hands_back(self):
        self.write("# R\n")                                               # half-written, no status
        self.assertEqual(self.scan(), [])
        self.write("Status: needs-verification\n")
        self.assertEqual(self.scan(), [("07", "needs-verification")])
        self.assertEqual(self.scan(), [])                                 # already seen
        self.write("Status: changes-requested\n\n## Lead notes\n")       # lead parks it
        self.assertEqual(self.scan(), [])
        self.write("Status: needs-verification\n\n## Fixes\n")           # worker hands back
        self.assertEqual(self.scan(), [("07", "needs-verification")])
        self.write("Status: verified\n\n## Lead verification\n")         # lead closes it
        self.assertEqual(self.scan(), [])

    def test_blocked_wakes(self):
        self.write("**Status:** blocked\n")
        self.assertEqual(self.scan(), [("07", "blocked")])


class NoCommitCheck(unittest.TestCase):
    base = {"heads": {".": "a" * 40, "sub": "b" * 40}, "staged": {".": ["pre.md"], "sub": []}}

    def test_unchanged_is_clean(self):
        self.assertEqual(fleet.compare_snapshots(self.base, self.base), [])

    def test_moved_head_and_new_staging_are_reported(self):
        after = {"heads": {".": "a" * 40, "sub": "c" * 40}, "staged": {".": ["pre.md", "new.ts"], "sub": []}}
        problems = fleet.compare_snapshots(self.base, after)
        self.assertEqual(len(problems), 2)
        self.assertIn("HEAD moved in sub", problems[0])
        self.assertIn("new.ts", problems[1])

    def test_previously_staged_files_are_not_blamed(self):
        after = {"heads": self.base["heads"], "staged": {".": ["pre.md"], "sub": []}}
        self.assertEqual(fleet.compare_snapshots(self.base, after), [])


class Accounts(unittest.TestCase):
    def config(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(text)
        return fleet.load_config(Path(fh.name))

    def test_extends_copies_parent_but_not_enabled(self):
        cfg = self.config('[providers.claude-co]\nmax = 2\n'
                          '[providers.claude2]\nextends = "claude"\nenv = { CLAUDE_CONFIG_DIR = "/a/b" }\n')
        co = cfg["providers"]["claude-co"]
        self.assertFalse(co["enabled"])
        self.assertEqual((co["max"], co["plugins"], co["tiers"]["heavy"]["model"]), (2, False, "opus"))
        self.assertNotIn("enabled", cfg["providers"]["claude2"])
        self.assertEqual(cfg["providers"]["claude2"]["login"], "claude auth status")

    def test_extends_rejects_cycles_and_unknown_parents(self):
        with self.assertRaises(fleet.FleetError):
            self.config('[providers.a]\nextends = "b"\n[providers.b]\nextends = "a"\n')
        with self.assertRaises(fleet.FleetError):
            self.config('[providers.a]\nextends = "nope"\n')

    def test_login_env_ignores_the_leads_account(self):
        cfg = fleet.load_config(Path("/nonexistent/config.toml"))
        old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = "/lead/account"
        try:
            self.assertNotIn("CLAUDE_CONFIG_DIR", fleet.child_env(cfg["providers"]["claude"]))
            self.assertEqual(fleet.child_env(cfg["providers"]["claude-co"])["CLAUDE_CONFIG_DIR"],
                             str(Path("~/.claude-co").expanduser()))
            self.assertEqual(fleet.child_env(cfg["providers"]["cursor"])["CLAUDE_CONFIG_DIR"], "/lead/account")
        finally:
            if old is None:
                os.environ.pop("CLAUDE_CONFIG_DIR")
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = old

    def test_account_add_appends_and_refuses_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text("# mine\n[defaults]\nmax_parallel = 3")
            old = fleet.USER_CONFIG
            fleet.USER_CONFIG = path
            try:
                args = ["account", "add", "codex-2", "--from", "codex", "--dir", "~/.codex-2"]
                self.assertEqual(fleet.main(args), 0)
                text = path.read_text()
                self.assertTrue(text.startswith("# mine\n"))
                p = fleet.load_config(path)["providers"]["codex-2"]
                self.assertEqual((p["enabled"], p["max"], p["bin"]), (True, 1, "codex"))
                self.assertIn("CODEX_HOME=" + str(Path("~/.codex-2").expanduser()),
                              fleet.build_launch(p, "m", "high", "go"))
                self.assertEqual(fleet.main(args), 1)
                self.assertEqual(fleet.main(["account", "add", "claude-co", "--from", "claude", "--dir", "/x"]), 1)
                self.assertEqual(fleet.main(["account", "add", "x", "--from", "cursor", "--dir", "/x"]), 1)
                self.assertEqual(path.read_text(), text)
            finally:
                fleet.USER_CONFIG = old

    def test_doctor_fails_when_two_providers_share_an_account(self):
        login = 'echo \'{"loggedIn": true, "email": "%s"}\''
        cfg = {"providers": {
            name: {"bin": "sh", "login": login % email, "login_ok": '"loggedIn": true'}
            for name, email in (("a", "one@x"), ("b", "one@x"), ("c", "two@x"))}}
        orig = fleet.cmux
        fleet.cmux = lambda *a, **k: ""
        try:
            args = type("A", (), {"providers": ["a", "c"]})()
            self.assertEqual(fleet.cmd_doctor(args, cfg), 0)
            args.providers = ["a", "b", "c"]
            self.assertEqual(fleet.cmd_doctor(args, cfg), 1)
        finally:
            fleet.cmux = orig


class Binary(unittest.TestCase):
    def test_env_prefix_is_skipped(self):
        self.assertEqual(fleet.first_binary("CLAUDE_CONFIG_DIR=$HOME/.claude-co claude"), "claude")
        self.assertEqual(fleet.first_binary("cursor-agent"), "cursor-agent")


if __name__ == "__main__":
    unittest.main()
