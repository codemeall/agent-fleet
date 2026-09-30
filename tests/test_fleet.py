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


class Models(unittest.TestCase):
    def test_listing_formats(self):
        text = ("Available models\n\nauto - Auto (default)\ngrok-4.7-high - Grok 4.7  High\n"
                "Fetching available models...\ngemini-3.1-pro-high\tGemini 3.1 Pro (High)\n")
        self.assertEqual(list(fleet.parse_models(text)), ["auto", "grok-4.7-high", "gemini-3.1-pro-high"])
        catalog = ('{"models": [{"slug": "sol", "visibility": "list", "supported_reasoning_levels": '
                   '[{"effort": "low"}, {"effort": "high"}]}, {"slug": "secret", "visibility": "hide"}]}')
        self.assertEqual(fleet.parse_models(catalog), {"sol": {"low", "high"}})

    def test_catalog_efforts_are_checked(self):
        cat = '{"models": [{"slug": "sol", "supported_reasoning_levels": [{"effort": "low"}]}]}'
        p = {"models": f"echo '{cat}'", "tiers": {"heavy": {"model": "sol", "effort": "high"}}}
        self.assertEqual(fleet.model_problems(p)[0], ["tiers.heavy=sol@high"])
        p["tiers"]["heavy"]["effort"] = "low"
        self.assertEqual(fleet.model_problems(p)[0], [])

    def test_tier_models_are_checked_against_the_listing(self):
        p = {"models": "printf 'a - A\\nb - B\\n'", "tiers": {"heavy": {"model": "a"}, "review": {"model": "z"}}}
        self.assertEqual(fleet.model_problems(p), (["tiers.review=z"], "models NOT OFFERED: tiers.review=z"))
        p["tiers"]["review"]["model"] = "b"
        self.assertEqual(fleet.model_problems(p), ([], "models verified"))
        self.assertIn("model list failed", fleet.model_problems({"models": "exit 3"})[1])

    def test_unlisted_cli_shows_its_configured_default(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "config.toml").write_text('model = "gpt-x"\n[tui]\nmodel = "other"\n')
            p = {"account_env": "CODEX_HOME", "env": {"CODEX_HOME": d}, "model_config": "config.toml"}
            self.assertEqual(fleet.model_problems(p), ([], "models unverified; CLI default is gpt-x"))
        self.assertEqual(fleet.model_problems({"model_note": "aliases"}), ([], "aliases"))

    def test_doctor_fails_on_a_tier_model_the_account_lacks(self):
        login = {"bin": "sh", "login": "true", "login_ok": "", "models": "printf 'a - A\\n'"}
        cfg = {"providers": {"x": dict(login, login_ok="", tiers={"heavy": {"model": "a"}})}}
        orig = fleet.cmux
        fleet.cmux = lambda *a, **k: ""
        try:
            args = type("A", (), {"providers": ["x"]})()
            cfg["providers"]["x"]["login"] = "echo ok"
            cfg["providers"]["x"]["login_ok"] = "ok"
            self.assertEqual(fleet.cmd_doctor(args, cfg), 0)
            cfg["providers"]["x"]["tiers"]["heavy"]["model"] = "gone"
            self.assertEqual(fleet.cmd_doctor(args, cfg), 1)
        finally:
            fleet.cmux = orig


class AllowList(unittest.TestCase):
    def config(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
            fh.write(text)
        return fleet.load_config(Path(fh.name))

    def test_user_list_replaces_shipped_one_and_extends_inherits_it(self):
        cfg = self.config('[providers.cursor.models_allow]\n"a" = "xai"\n'
                          '[providers.claude]\nmodels_allow = ["opus"]\n')
        self.assertEqual(fleet.allowed_models(cfg["providers"]["cursor"]),
                         {"a": "xai", "grok-4.7-high": "xai", "kimi-k3-high": "moonshot",
                          "grok-4.7-high-fast": "xai", "muse-spark-1.3-max": "meta"})
        self.assertEqual(fleet.allowed_models(cfg["providers"]["claude-co"]), {"opus": None, "sonnet": None})
        with self.assertRaises(fleet.FleetError):
            self.config('[providers.claude]\nmodels_allow = "opus"\n')

    def test_allow_narrows_a_live_list_and_reports_stale_entries(self):
        p = {"models": "printf 'a - A\\nb - B\\nc - C\\n'", "models_allow": {"b": "x", "gone": "y"},
             "tiers": {"heavy": {"model": "a"}, "light": {"model": "retired"}}}
        offered, stale = fleet.usable_models(p)
        self.assertEqual((sorted(offered), sorted(stale)), (["a", "b"], ["gone", "retired"]))
        missing, note = fleet.model_problems(p)
        self.assertEqual(missing, ["tiers.light=retired"])
        self.assertIn("no longer offered: gone", note)

    def test_allow_is_the_list_when_the_cli_cannot_list(self):
        p = {"models_allow": ["opus", "sonnet"], "tiers": {"heavy": {"model": "opus"}}}
        self.assertEqual(fleet.model_problems(p), ([], "models checked against models_allow"))
        fleet.check_allowed(p, "claude", "sonnet")
        with self.assertRaises(fleet.FleetError):
            fleet.check_allowed(p, "claude", "claude-opus-5-5")

    def test_a_tier_override_is_allowed_without_editing_the_list(self):
        cfg = self.config('[providers.cursor.tiers.heavy]\nmodel = "composer-9"\nfamily = "cursor"\n'
                          '[providers.claude.tiers.heavy]\nmodel = "claude-opus-5-5"\n')
        cursor, claude = cfg["providers"]["cursor"], cfg["providers"]["claude"]
        fleet.check_allowed(cursor, "cursor", "composer-9")
        fleet.check_allowed(claude, "claude", "claude-opus-5-5")
        self.assertEqual(fleet.model_family(cursor, "standard", "composer-9"), "cursor")
        p = dict(cursor, models="printf 'composer-9 - C\\n'")
        self.assertIn("tiers.standard=kimi-k3-high", fleet.model_problems(p)[0])
        self.assertNotIn("tiers.heavy=composer-9", fleet.model_problems(p)[0])

    def test_plans_outside_the_list_are_refused_and_families_come_from_it(self):
        p = {"models_allow": {"kimi": "moonshot"}, "family": "mixed"}
        fleet.check_allowed(p, "cursor", "kimi")
        with self.assertRaises(fleet.FleetError):
            fleet.check_allowed(p, "cursor", "other")
        fleet.check_allowed({}, "codex", "anything")
        self.assertEqual(fleet.model_family(p, "heavy", "kimi"), "moonshot")


class Efforts(unittest.TestCase):
    def test_claude_effort_levels_are_checked(self):
        cfg = fleet.load_config(Path("/nonexistent/config.toml"))
        claude = cfg["providers"]["claude"]
        self.assertEqual(fleet.model_problems(claude)[0], [])
        fleet.check_effort(claude, "claude", "xhigh")
        with self.assertRaises(fleet.FleetError):
            fleet.check_effort(claude, "claude", "hgh")
        fleet.check_effort(cfg["providers"]["codex"], "codex", "anything")
        bad = dict(claude, tiers={"heavy": {"model": "opus", "effort": "ultra"}})
        self.assertEqual(fleet.model_problems(bad)[0], ["tiers.heavy=opus@ultra"])
        self.assertEqual(fleet.model_problems(dict(cfg["providers"]["claude-co"], tiers=bad["tiers"]))[0],
                         ["tiers.heavy=opus@ultra"])


class Binary(unittest.TestCase):
    def test_env_prefix_is_skipped(self):
        self.assertEqual(fleet.first_binary("CLAUDE_CONFIG_DIR=$HOME/.claude-co claude"), "claude")
        self.assertEqual(fleet.first_binary("cursor-agent"), "cursor-agent")


if __name__ == "__main__":
    unittest.main()
