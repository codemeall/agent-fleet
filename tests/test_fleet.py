"""Unit tests for the pure parts of skill/bin/fleet. Run: python3 -m unittest discover tests"""
import contextlib
import importlib.machinery
import io
import importlib.util
import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The suite may run inside a Claude Code or Codex session; its own context must not leak into Fleet output.
for _var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
    os.environ.pop(_var, None)
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
        self.assertEqual(fleet.build_launch(p["agy"], "gemini-3.1-pro-high", "high", "go"),
                         "agy --model gemini-3.1-pro-high --effort high --mode accept-edits -i go")
        self.assertEqual(fleet.build_launch(p["opencode"], "openrouter/z-ai/glm-5.3", None, "go"),
                         "opencode --model openrouter/z-ai/glm-5.3 --prompt go")

    def test_disabled_agy_and_opencode_adapters_are_complete(self):
        # They ship disabled, so the every-tier test above skips them.
        for name in ("agy", "opencode"):
            p = self.cfg["providers"][name]
            self.assertEqual(p["quit"], ["/exit", "enter"])
            for tier in ("heavy", "standard", "light", "review"):
                with self.subTest(provider=name, tier=tier):
                    model, effort = fleet.resolve_model(p, tier, None, None)
                    fleet.check_allowed(p, name, model)
                    fleet.check_effort(p, name, effort)
                    self.assertIsNotNone(fleet.model_family(p, tier, model))
                    self.assertNotIn("{", fleet.build_launch(p, model, effort, "Read /x/p.md"))
        agy = self.cfg["providers"]["agy"]
        self.assertEqual(fleet.model_family(agy, "standard", "claude-opus-4-6-thinking"), "anthropic")
        self.assertEqual(fleet.model_family(agy, "standard", "gemini-3.7-flash-high"), "google")

    def test_claude_names_its_session_and_can_resume_it(self):
        p, sid = self.cfg["providers"], "0b6c4a52-7c1e-4f0e-9d2a-3f1e2d4c5b6a"
        self.assertTrue(fleet.build_launch(p["claude"], "opus", "high", "go", session=sid)
                        .endswith(f"claude --session-id {sid} --model opus --effort high go"))
        self.assertTrue(fleet.build_launch(p["claude-co"], "opus", "high", "go", session=sid, resume=True)
                        .endswith(f"claude --resume {sid} --model opus --effort high go"))
        self.assertTrue(fleet.resumable(p["claude-co"]))
        self.assertFalse(fleet.resumable(p["codex"]))
        self.assertNotIn(sid, fleet.build_launch(p["codex"], "m", "high", "go", session=sid))

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

    def test_doctor_reports_companions_and_skill_versions_without_failing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, project = Path(tmp, "home"), Path(tmp, "project")
            for skill in ("cmux", "grill-me"):
                (home / "claude" / skill).mkdir(parents=True)
                (home / "claude" / skill / "SKILL.md").write_text("x")
            (project / ".agents/skills/to-spec").mkdir(parents=True)
            (project / ".agents/skills/to-spec/SKILL.md").write_text("x")
            (home / "codex/fleet/bin").mkdir(parents=True)
            (home / "codex/fleet/SKILL.md").write_text("x")
            (home / "codex/fleet/bin/fleet").write_text('VERSION = "0.0.1"\n')
            dirs, cmux, cwd = fleet.HARNESS_SKILL_DIRS, fleet.cmux, os.getcwd()
            fleet.HARNESS_SKILL_DIRS = {h: str(home / h) for h in ("claude", "codex")}
            fleet.cmux = lambda *a, **k: ""
            os.chdir(project)
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = fleet.cmd_doctor(type("A", (), {"providers": []})(), {"providers": {}})
            finally:
                fleet.HARNESS_SKILL_DIRS, fleet.cmux = dirs, cmux
                os.chdir(cwd)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn(f"fleet         {fleet.VERSION}", text)
        self.assertIn("skill@claude  not installed", text)
        self.assertIn(f"skill@codex   installed (0.0.1; this runtime is {fleet.VERSION})", text)
        self.assertRegex(text, r"\n  manaflow-ai/cmux\n    cmux +claude  \(")
        self.assertRegex(text, r"\n  mattpocock/skills\n    grill-with-docs +not found")
        self.assertRegex(text, r"\n    grill-me +claude  \(")
        self.assertRegex(text, r"\n    to-spec +codex \(project\)  \(")
        self.assertIn("not found: npx skills add mattpocock/skills --skill to-tickets", text)
        self.assertIn("not found: npx skills add mattpocock/skills --skill grill-with-docs", text)
        self.assertRegex(text, r"\n  codemeall/feature-init\n    feature-docs +not found: "
                               r"npx skills add codemeall/feature-init --skill feature-docs  \(")

    def test_version_needs_no_config(self):
        old = fleet.USER_CONFIG
        with tempfile.TemporaryDirectory() as tmp:
            fleet.USER_CONFIG = Path(tmp, "config.toml")
            fleet.USER_CONFIG.write_text("not [valid toml")
            try:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(fleet.main(["version"]), 0)
                    with self.assertRaises(SystemExit) as exit_:
                        fleet.main(["--version"])
            finally:
                fleet.USER_CONFIG = old
        self.assertEqual(exit_.exception.code, 0)
        self.assertEqual(out.getvalue().splitlines()[0], f"fleet {fleet.VERSION}")
        self.assertEqual(out.getvalue().splitlines()[-1], f"fleet {fleet.VERSION}")


class Models(unittest.TestCase):
    def test_listing_formats(self):
        text = ("Available models\n\nauto - Auto (default)\ngrok-4.7-high - Grok 4.7  High\n"
                "Fetching available models...\ngemini-3.1-pro-high\tGemini 3.1 Pro (High)\n")
        self.assertEqual(list(fleet.parse_models(text)), ["auto", "grok-4.7-high", "gemini-3.1-pro-high"])
        catalog = ('{"models": [{"slug": "sol", "visibility": "list", "supported_reasoning_levels": '
                   '[{"effort": "low"}, {"effort": "high"}]}, {"slug": "secret", "visibility": "hide"}]}')
        self.assertEqual(fleet.parse_models(catalog), {"sol": {"low", "high"}})

    def test_bare_provider_model_lines(self):
        text = "Models:\nopencode/big-pickle\nopenrouter/z-ai/glm-5.3  \nhuggingface/deepseek-ai/DeepSeek-V4-Pro\nloading\n"
        self.assertEqual(list(fleet.parse_models(text)),
                         ["opencode/big-pickle", "openrouter/z-ai/glm-5.3", "huggingface/deepseek-ai/DeepSeek-V4-Pro"])

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


class PanePlacement(unittest.TestCase):
    def pane(self, ref, surfaces, width=800, height=600):
        # Surface UUIDs equal their refs here, so `retired` (UUIDs) and `busy` (refs) read alike.
        return {"ref": ref, "id": "uuid-" + ref, "index": int(ref.split(":")[1]) - 1, "surface_refs": surfaces,
                "surface_ids": list(surfaces), "pixel_frame": {"width": width, "height": height}}

    def test_splits_the_largest_pane_along_its_long_side_below_the_limit(self):
        panes = [self.pane("pane:1", ["s:lead"], 1200, 900), self.pane("pane:2", ["s:w1"], 600, 900)]
        self.assertEqual(fleet.pick_pane(panes[:1], set(), set(), "pane:1"), ("split", "s:lead", "right"))
        # A pane with only a finished worker or an idle shell is still split, never given another tab.
        self.assertEqual(fleet.pick_pane(panes, {"s:w1"}, set(), "pane:1"), ("split", "s:lead", "right"))
        tall = [self.pane("pane:1", ["s:lead"], 600, 900), self.pane("pane:2", ["s:w1", "s:w2"], 500, 900)]
        tall[1]["selected_surface_ref"] = "s:w2"
        self.assertEqual(fleet.pick_pane(tall, set(), set(), "pane:1"), ("split", "s:lead", "down"))
        self.assertEqual(fleet.pick_pane(tall, set(), set(), "pane:1", "pane:2"), ("split", "s:w2", "down"))

    def full(self):
        return [self.pane("pane:1", ["s:lead"]), self.pane("pane:2", ["s:live"]), self.pane("pane:3", ["s:sh"]),
                self.pane("pane:4", ["s:done", "s:sh2"]), self.pane("pane:5", ["s:old", "s:done2"])] + \
               [self.pane(f"pane:{n}", [f"s:live{n}"]) for n in range(6, 9)]

    def test_at_the_limit_replaces_a_finished_workers_pane_before_adding_a_tab(self):
        retired, busy = {"s:done", "s:old", "s:done2", "s:lead"}, {"s:live", "s:live6", "s:live7", "s:live8"}
        self.assertEqual(fleet.pick_pane(self.full(), retired, busy, "pane:1"), ("replace", "pane:5", None))
        self.assertEqual(fleet.pick_pane(self.full(), retired, busy, "pane:1", "pane:4"), ("tab", "pane:4", None))
        for want in ("uuid-pane:4", "3"):  # --pane also takes a UUID or an index
            self.assertEqual(fleet.pick_pane(self.full(), retired, busy, "pane:1", want), ("tab", "pane:4", None))
        # Nothing to replace: a tab goes in the first idle pane, never the lead's.
        self.assertEqual(fleet.pick_pane(self.full(), {"s:done"}, busy, "pane:1"), ("tab", "pane:3", None))
        for want in ("pane:1", "pane:2"):  # the lead's, a live worker's
            with self.assertRaisesRegex(fleet.FleetError, "max 8"):
                fleet.pick_pane(self.full(), retired, busy, "pane:1", want)
        live = busy | {"s:sh", "s:sh2", "s:old"}
        with self.assertRaisesRegex(fleet.FleetError, "every pane has a live worker"):
            fleet.pick_pane(self.full(), set(), live, "pane:1")
        with self.assertRaisesRegex(fleet.FleetError, "no pane pane:9"):
            fleet.pick_pane(self.full(), retired, busy, "pane:1", "pane:9")


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


class Retirements(unittest.TestCase):
    CATALOG = ('{"models": [{"slug": "old", "upgrade": {"model": "new", "retirement_at": "2026-10-14T19:00:00Z"}},'
               ' {"slug": "new", "upgrade": null}]}')

    def test_catalog_upgrade_notices_are_parsed(self):
        self.assertEqual(fleet.parse_retirements(self.CATALOG), {"old": ("new", "2026-10-14")})
        self.assertEqual(fleet.parse_retirements("a - A"), {})

    def test_doctor_warns_but_does_not_fail_on_a_retiring_tier_model(self):
        p = {"models": f"echo '{self.CATALOG}'", "tiers": {"heavy": {"model": "old"}, "review": {"model": "old"}}}
        missing, note = fleet.model_problems(p)
        self.assertEqual(missing, [])
        self.assertTrue(note.endswith("; RETIRING: old retires 2026-10-14, switch to new"))
        p["tiers"] = {"heavy": {"model": "new"}}
        self.assertNotIn("RETIRING", fleet.model_problems(p)[1])


class Binary(unittest.TestCase):
    def test_env_prefix_is_skipped(self):
        self.assertEqual(fleet.first_binary("CLAUDE_CONFIG_DIR=$HOME/.claude-co claude"), "claude")
        self.assertEqual(fleet.first_binary("cursor-agent"), "cursor-agent")


class LeadContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        zones = {"warn_at": 100_000, "dumb_at": 125_000}
        self.cfg = {"lead_context": {"claude": zones, "codex": zones}}

    def claude_log(self, sid, entries):
        f = self.home / "claude" / "projects" / "-repo" / f"{sid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return f

    def codex_log(self, tid, entries):
        f = self.home / "codex" / "sessions" / "2026" / "10" / "06" / f"rollout-2026-10-06T00-00-00-{tid}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return f

    @staticmethod
    def assistant(cache_read, sidechain=False):
        return {"type": "assistant", "isSidechain": sidechain, "message": {"usage": {
            "input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": cache_read,
            "output_tokens": 500}}}

    @staticmethod
    def token_count(last, total):
        return {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": total, "output_tokens": 10},
            "last_token_usage": {"input_tokens": last, "output_tokens": 10}}}}

    def env(self, **ids):
        env = {"CLAUDE_CONFIG_DIR": str(self.home / "claude"), "CODEX_HOME": str(self.home / "codex")}
        if "claude" in ids:
            env["CLAUDE_CODE_SESSION_ID"] = ids["claude"]
        if "codex" in ids:
            env["CODEX_THREAD_ID"] = ids["codex"]
        return env

    def test_claude_reads_the_latest_main_chain_usage_and_skips_subagents(self):
        sid = "11111111-2222-3333-4444-555555555555"
        self.claude_log(sid, [self.assistant(50_000), {"type": "user"}, self.assistant(128_000),
                              self.assistant(900_000, sidechain=True)])
        r = fleet.lead_reading(self.cfg, self.env(claude=sid))
        self.assertEqual((r["host"], r["tokens"], r["zone"]), ("claude", 129_502, "dumb"))

    def test_codex_uses_last_call_usage_not_the_session_total(self):
        tid = "01a10dd0-cc90-7880-884b-11a5b4ba24dc"
        self.codex_log(tid, [self.token_count(20_000, 40_000), {"type": "event_msg", "payload": {"type": "task_complete"}},
                             self.token_count(104_990, 600_000)])
        r = fleet.lead_reading(self.cfg, self.env(codex=tid))
        self.assertEqual((r["host"], r["tokens"], r["zone"]), ("codex", 105_000, "warning"))

    def test_zone_boundaries(self):
        tid = "01a10dd0-cc90-7880-884b-11a5b4ba24dc"
        for last, zone in ((99_989, "smart"), (99_990, "warning"), (124_989, "warning"), (124_990, "dumb")):
            with self.subTest(tokens=last + 10):
                self.codex_log(tid, [self.token_count(last, last)])
                self.assertEqual(fleet.lead_reading(self.cfg, self.env(codex=tid))["zone"], zone)

    def test_not_a_lead_without_a_session_id_a_log_or_a_recorded_call(self):
        self.assertIsNone(fleet.lead_reading(self.cfg, self.env()))
        self.assertIsNone(fleet.lead_reading(self.cfg, self.env(claude="99999999-0000-0000-0000-000000000000")))
        self.assertIsNone(fleet.lead_reading(self.cfg, self.env(claude="../../etc/passwd")))
        tid = "01a10dd0-cc90-7880-884b-11a5b4ba24dc"
        self.codex_log(tid, [{"type": "session_meta", "payload": {}}])
        self.assertIsNone(fleet.lead_reading(self.cfg, self.env(codex=tid)))

    def test_a_stale_log_is_an_earlier_session_not_this_lead(self):
        sid = "11111111-2222-3333-4444-555555555555"
        f = self.claude_log(sid, [self.assistant(130_000)])
        later = f.stat().st_mtime + fleet.LEAD_STALE_S + 1
        self.assertIsNone(fleet.lead_reading(self.cfg, self.env(claude=sid), now=later))

    def test_with_both_ids_the_most_recently_written_log_is_the_lead(self):
        sid, tid = "11111111-2222-3333-4444-555555555555", "01a10dd0-cc90-7880-884b-11a5b4ba24dc"
        old = self.claude_log(sid, [self.assistant(130_000)])
        os.utime(old, (old.stat().st_mtime - 60, old.stat().st_mtime - 60))
        self.codex_log(tid, [self.token_count(30_000, 30_000)])
        self.assertEqual(fleet.lead_reading(self.cfg, self.env(claude=sid, codex=tid))["host"], "codex")

    def test_notices(self):
        r = {"tokens": 30_000, "zone": "smart", "warn_at": 100_000, "dumb_at": 125_000}
        self.assertIsNone(fleet.lead_notice(r, "demo", None))
        self.assertIn("nearing the dumb zone", fleet.lead_notice(dict(r, tokens=104_000, zone="warning"), "demo", None))
        self.assertIn("LEAD 131K dumb zone", fleet.lead_notice(dict(r, tokens=131_000, zone="dumb"), "demo", 120_000))
        compacted = fleet.lead_notice(r, "demo", 140_000)
        self.assertIn("compacted 140K -> 30K", compacted)
        self.assertIn("fleet resume demo", compacted)
        self.assertIsNone(fleet.lead_notice(r, "demo", 60_000))  # a drop below the warning line is not a compaction

    def test_a_server_side_tool_step_does_not_inflate_the_reading(self):
        # Shape of a real Claude Code entry for a response that called the advisor: the top-level usage sums all steps.
        sid = "11111111-2222-3333-4444-555555555555"
        entry = {"type": "assistant", "isSidechain": False, "message": {"usage": {
            "input_tokens": 4, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 188_567, "output_tokens": 590,
            "iterations": [
                {"type": "message", "input_tokens": 2, "cache_creation_input_tokens": 679, "cache_read_input_tokens": 93_944,
                 "output_tokens": 186},
                {"type": "advisor_message", "input_tokens": 96_505, "output_tokens": 4590},
                {"type": "message", "input_tokens": 2, "cache_creation_input_tokens": 1321, "cache_read_input_tokens": 94_623,
                 "output_tokens": 404}]}}}
        self.claude_log(sid, [entry])
        r = fleet.lead_reading(self.cfg, self.env(claude=sid))
        self.assertEqual(r["tokens"], 96_350)
        self.assertIsNone(fleet.lead_notice(dict(r, tokens=97_720, zone="smart"), "demo", r["tokens"]))

    def test_defaults_set_no_zones_so_the_owner_decides(self):
        cfg = fleet.load_config(Path(self.temp.name) / "missing.toml")
        self.assertEqual(cfg["lead_context"], {})
        sid, tid = "11111111-2222-3333-4444-555555555555", "01a10dd0-cc90-7880-884b-11a5b4ba24dc"
        self.claude_log(sid, [self.assistant(900_000)])
        self.assertEqual(fleet.lead_reading(cfg, self.env(claude=sid))["zone"], "off")
        self.codex_log(tid, [self.token_count(200_000, 200_000)])
        r = fleet.lead_reading(cfg, self.env(codex=tid))
        self.assertEqual((r["zone"], r["warn_at"]), ("off", None))
        self.assertIsNone(fleet.lead_notice(r, "demo", None))
        self.assertIn("compacted 200K -> 30K", fleet.lead_notice(dict(r, tokens=30_000), "demo", 200_000))

    def test_config_sets_zones_per_host_and_rejects_bad_values(self):
        cfg = Path(self.temp.name) / "config.toml"
        cfg.write_text("[lead_context.claude]\nwarn_at = 500000\ndumb_at = 600000\n")
        self.assertEqual(fleet.load_config(cfg)["lead_context"], {"claude": {"warn_at": 500_000, "dumb_at": 600_000}})
        for body in ("[lead_context.claude]\nwarn_at = 700000\ndumb_at = 600000\n",
                     "[lead_context.claude]\nwarn_at = true\ndumb_at = 600000\n",
                     "[lead_context.codex]\nwarn_at = 90000\n",  # both are needed
                     "[lead_context.claude]\nwarn = 90000\n",
                     "[lead_context]\nwarn_at = 90000\n",
                     "lead_context = 5\n"):
            with self.subTest(body=body):
                cfg.write_text(body)
                with self.assertRaises(fleet.FleetError):
                    fleet.load_config(cfg)


if __name__ == "__main__":
    unittest.main()
