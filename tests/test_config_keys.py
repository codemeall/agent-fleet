"""Unknown config keys (#12): load_config warns on stderr, naming the file, section and key, and still loads."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
for _var in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
    os.environ.pop(_var, None)
_loader = importlib.machinery.SourceFileLoader("fleet_config_keys", str(ROOT / "skill" / "bin" / "fleet"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
fleet = importlib.util.module_from_spec(_spec)
_loader.exec_module(fleet)

NEXT_VERSION = "becomes an error in the next version"


class ConfigKeys(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def load(self, text: str, name: str = "config.toml") -> tuple[dict, str]:
        path = self.dir / name
        path.write_text(text)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = fleet.load_config(path)
        return cfg, err.getvalue()

    def test_a_typo_warns_naming_file_section_key_and_the_closest_match(self):
        cfg, err = self.load('[providers.codex.tiers.standard]\nmodle = "x"\n')
        self.assertEqual(err.count("\n"), 1, err)
        self.assertIn(f'warning: {self.dir / "config.toml"} [providers.codex.tiers.standard]: '
                      f'unknown key "modle" (did you mean "model"?)', err)
        self.assertIn(NEXT_VERSION, err)
        self.assertEqual(cfg["providers"]["codex"]["tiers"]["standard"]["model"], "gpt-6.1-sol")  # still loads

    def test_every_section_is_checked(self):
        for text, where, key, close in (
                ("defualts = 1\n", "the top level", "defualts", "defaults"),
                ("[defaults]\nmax_paralel = 2\n", "[defaults]", "max_paralel", "max_parallel"),
                ("[defaults]\ncheck_polcy = \"warn\"\n", "[defaults]", "check_polcy", "check_policy"),
                ('[providers.codex]\nlauch = "x"\n', "[providers.codex]", "lauch", "launch"),
                ('[providers.mine]\nextends = "codex"\nbin_alias = ["c"]\n', "[providers.mine]", "bin_alias",
                 "bin_aliases"),
                ('[providers.claude.profiles.reviewer]\nargs = ""\nlevel = "read-only"\n',
                 "[providers.claude.profiles]", "reviewer", "review"),
                ('[providers.claude.profiles.review]\nargz = ""\nlevel = "read-only"\n',
                 "[providers.claude.profiles.review]", "argz", "args"),
                ("[lead_context.claude]\nwarn_at = 1\ndumb_at = 2\n[providers.codex]\nzzz = 1\n",
                 "[providers.codex]", "zzz", None)):
            with self.subTest(key=key):
                _, err = self.load(text)
                hint = f' (did you mean "{close}"?)' if close else ";"
                self.assertIn(f'{where}: unknown key "{key}"{hint}', err)
                self.assertIn(NEXT_VERSION, err)

    def test_a_dotted_adapter_name_is_quoted_in_the_section(self):
        _, err = self.load('[providers."my.codex"]\nextends = "codex"\nmodle = "x"\n')
        self.assertIn('[providers."my.codex"]: unknown key "modle"', err)

    def test_free_form_tables_and_names_from_this_release_never_warn(self):
        _, err = self.load(
            '[defaults]\nscope_permissions = true\nreview_transport = "headless"\ncheck_policy = "warn"\n'
            '[providers.codex]\nenv = { ANYTHING = "1" }\nheadless = "x"\nreview_transport = "tui"\n'
            'hooks = "claude-settings"\nmodel_families = { "gpt-x" = "openai" }\n'
            'family_prefixes = { "gpt-" = "openai" }\nmodels_allow = { "gpt-x" = "openai" }\n'
            '[providers.codex.tiers.custom]\nmodel = "gpt-x"\neffort = "high"\nfamily = "openai"\n')
        self.assertEqual(err, "")

    def test_the_shipped_providers_toml_loads_without_a_warning(self):
        _, err = self.load("")
        self.assertEqual(err, "")

    def test_config_example_loads_without_a_warning_even_with_every_key_uncommented(self):
        text = (ROOT / "config.example.toml").read_text()
        self.assertEqual(self.load(text)[1], "")
        setting = re.compile(r"^# ?(\[[\w.\"-]+\]|[a-z_]+ = .*)$")
        lines = [setting.sub(r"\1", line) for line in text.splitlines()]
        self.assertGreater(sum(a != b for a, b in zip(lines, text.splitlines())), 20)  # really uncommented
        cfg, err = self.load("\n".join(lines) + "\n")
        self.assertEqual(err, "")
        self.assertEqual(cfg["defaults"]["workspace"], "workspace:3")
        self.assertEqual(cfg["defaults"]["check_policy"], "refuse")

    def test_an_unknown_key_in_providers_toml_names_that_file(self):
        skill = self.dir / "skill"
        skill.mkdir()
        text = (ROOT / "skill/providers.toml").read_text().replace("max = 2\n", "max = 2\nmaxx = 2\n", 1)
        (skill / "providers.toml").write_text(text)
        err = io.StringIO()
        with mock.patch.object(fleet, "SKILL_DIR", skill), contextlib.redirect_stderr(err):
            fleet.load_config(self.dir / "missing.toml")
        self.assertIn(f'warning: {skill / "providers.toml"} [providers.claude]: unknown key "maxx" '
                      '(did you mean "max"?)', err.getvalue())

    def test_the_command_still_runs(self):
        path = self.dir / "config.toml"
        path.write_text('[providers.codex.tiers.standard]\nmodle = "x"\n')
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fleet, "USER_CONFIG", path), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = fleet.main(["providers"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn('unknown key "modle" (did you mean "model"?)', err.getvalue())
        self.assertIn("codex", out.getvalue())

    def test_check_policy_is_refuse_or_warn(self):
        self.assertEqual(self.load("")[0]["defaults"]["check_policy"], "refuse")
        self.assertEqual(self.load('[defaults]\ncheck_policy = "warn"\n')[0]["defaults"]["check_policy"], "warn")
        for bad in ('"off"', "true", '"Refuse"'):
            with self.subTest(value=bad), self.assertRaisesRegex(fleet.FleetError, "check_policy"):
                self.load(f"[defaults]\ncheck_policy = {bad}\n")

    def test_the_providers_md_table_lists_every_known_key(self):
        doc = (ROOT / "skill/references/providers.md").read_text()
        table = doc[doc.index("## Config keys"):]
        for key in [*fleet.DEFAULTS, *fleet.ADAPTER_FIELDS, *fleet.TIER_FIELDS,
                    *fleet.PROFILE_FIELDS, "warn_at", "dumb_at"]:
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", table)
