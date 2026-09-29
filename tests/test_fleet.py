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
            "codex -m gpt-6-sol -c model_reasoning_effort=high -s workspace-write -a on-request go")
        self.assertEqual(
            fleet.build_launch(p["cursor"], "grok-4.7-high", None, "go"),
            "cursor-agent --model grok-4.7-high --trust go")
        self.assertTrue(fleet.build_launch(p["claude-co"], "opus", "high", "go")
                        .startswith("CLAUDE_CONFIG_DIR=$HOME/.claude-co claude --model opus"))
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


class Binary(unittest.TestCase):
    def test_env_prefix_is_skipped(self):
        self.assertEqual(fleet.first_binary("CLAUDE_CONFIG_DIR=$HOME/.claude-co claude"), "claude")
        self.assertEqual(fleet.first_binary("cursor-agent"), "cursor-agent")


if __name__ == "__main__":
    unittest.main()
