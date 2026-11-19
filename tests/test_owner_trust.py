"""Doctor and init report missing folder trust and login. They never write a CLI's trust file or start a sign-in."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


def provider(name: str, **extra) -> dict:
    base = {"bin": name, "launch": "{bin} --model {model} {prompt}", "enabled": True,
            "profiles": {"worker": {"args": "", "level": "workspace-write"},
                         "review": {"args": "", "level": "read-only"}}}
    base.update(extra)
    return base


class TrustReads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.cache = self.home / "cache"
        env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CACHE_HOME": str(self.cache)})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = {"defaults": dict(fleet.DEFAULTS), "providers": {}}
        self.assertIs(fleet.DEFAULTS["auto_trust"], False)
        self.assertIs(self.cfg["defaults"]["auto_trust"], False)

    def claude(self, **extra) -> dict:
        return provider("claude", trust="claude-config", login="claude auth status",
                        login_ok='"loggedIn": true', **extra)

    def test_login_fix_names_the_sign_in_and_is_not_a_command_doctor_runs(self):
        self.assertEqual(fleet.login_fix("claude", self.claude()), "fix: claude auth login")
        self.assertEqual(fleet.login_fix("codex", {"login": "codex login status", "bin": "codex"}), "fix: codex login")
        self.assertEqual(fleet.login_fix("co", {"bin": "claude", "login": "claude auth status",
                                                "account_env": "CLAUDE_CONFIG_DIR",
                                                "env": {"CLAUDE_CONFIG_DIR": "~/claude-co"}}),
                         "fix: CLAUDE_CONFIG_DIR=" + __import__("shlex").quote(str(Path("~/claude-co").expanduser()))
                         + " claude auth login")

    def test_missing_claude_trust_names_both_paths_and_writes_nothing(self):
        gaps = fleet.untrusted_labels(self.cfg, self.claude(), self.repo)
        self.assertEqual(gaps, [f"checkout {self.repo}", f"worktree root {self.cache / 'agent-fleet' / 'worktrees'}"])
        self.assertFalse((self.home / ".claude.json").exists())

    def test_a_broken_trust_file_is_named_and_left_alone(self):
        path = self.home / ".claude.json"
        path.write_text("{not json")
        before = path.read_bytes()
        gaps = fleet.untrusted_labels(self.cfg, self.claude(), self.repo)
        self.assertIn("could not read trust data", gaps[0])
        self.assertEqual(path.read_bytes(), before)

    def test_trusted_paths_and_auto_trust_leave_no_gap(self):
        root = str(fleet.worktree_home(self.cfg).resolve())
        (self.home / ".claude.json").write_text(json.dumps({"projects": {
            str(self.repo.resolve()): {"hasTrustDialogAccepted": True},
            root: {"hasTrustDialogAccepted": True}}}))
        self.assertEqual(fleet.untrusted_labels(self.cfg, self.claude(), self.repo), [])
        self.cfg["defaults"]["auto_trust"] = True
        (self.home / ".claude.json").unlink()
        self.assertEqual(fleet.untrusted_labels(self.cfg, self.claude(), self.repo), [])
        self.assertFalse((self.home / ".claude.json").exists())

    def test_codex_and_agy_read_their_own_stores(self):
        codex = provider("codex", trust="codex-flag", account_env="CODEX_HOME", default_home=str(self.home / "codex"))
        agy = provider("agy", trust="agy-settings")
        checkout, tree = str(self.repo.resolve()), str(fleet.worktree_home(self.cfg).resolve())
        codex_home = self.home / "codex"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text(f'[projects."{checkout}"]\ntrust_level = "trusted"\n')
        agy_file = Path(fleet.AGY_SETTINGS).expanduser()
        agy_file.parent.mkdir(parents=True)
        agy_file.write_text(json.dumps({"trustedWorkspaces": [checkout, tree]}))
        self.assertEqual(fleet.untrusted_labels(self.cfg, codex, self.repo), [f"worktree root {tree}"])
        self.assertEqual(fleet.untrusted_labels(self.cfg, agy, self.repo), [])
        self.assertEqual((codex_home / "config.toml").read_text().count("trust_level"), 1)


class DoctorTrust(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.log = self.home / "cli.log"
        script = self.bin / "claude"
        script.write_text("#!/bin/bash\n"
                          f"echo \"$*\" >> {self.log}\n"
                          "if [ \"$1\" = auth ]; then\n"
                          "  if read -t 1 dummy; then echo stdin-had-data >> " + str(self.log) + "; fi\n"
                          "  echo '{\"loggedIn\": false}'\n"
                          "  exit 1\n"
                          "fi\n"
                          "echo '--model'\n")
        script.chmod(0o755)
        (self.bin / "cursor-agent").write_text("#!/bin/sh\necho '--model'\n")
        (self.bin / "cursor-agent").chmod(0o755)
        env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_CACHE_HOME": str(self.home / "cache"),
                                      "PATH": f"{self.bin}:{os.environ['PATH']}"})
        env.start()
        self.addCleanup(env.stop)

    def doctor(self, providers: dict, **defaults) -> tuple[int, str]:
        cfg = {"defaults": dict(fleet.DEFAULTS, **defaults), "providers": providers}
        out = io.StringIO()
        with patch.object(fleet, "repo_root", return_value=self.repo), \
                patch.object(fleet, "cmux", return_value=""), patch.object(fleet.Tmux, "check", return_value="tmux"), \
                patch.object(fleet, "skill_dirs", return_value={}), patch.object(fleet, "companion_skills", return_value={}), \
                patch.object(fleet, "trust_claude", side_effect=AssertionError("doctor wrote claude trust")), \
                patch.object(fleet, "trust_agy", side_effect=AssertionError("doctor wrote agy trust")), \
                contextlib.redirect_stdout(out):
            code = fleet.cmd_doctor(type("A", (), {"providers": list(providers)})(), cfg)
        return code, out.getvalue()

    def test_doctor_fails_untrusted_and_logged_out_and_names_the_fix(self):
        code, text = self.doctor({"claude": provider("claude", trust="claude-config", login="claude auth status",
                                                     login_ok='"loggedIn": true')})
        self.assertEqual(code, 1, text)
        self.assertEqual(text.count("owner action needed"), 0)
        self.assertIn(f"NOT TRUSTED: checkout {self.repo.resolve()}; worktree root ", text)
        self.assertIn("agent-fleet/worktrees", text)
        self.assertIn("fix: set auto_trust = true under [defaults]", text)
        self.assertIn("explicit owner opt-in; the default stays false", text)
        self.assertIn("LOGIN CHECK FAILED", text)
        self.assertIn("fix: claude auth login", text)
        log = self.log.read_text()
        self.assertNotIn("auth login", log)
        self.assertIn("auth status", log)
        self.assertNotIn("stdin-had-data", log)
        self.assertFalse((self.home / ".claude.json").exists())

    def test_auto_trust_does_not_fail_the_trust_check_or_write(self):
        code, text = self.doctor({"claude": provider("claude", trust="claude-config", login="echo ok", login_ok="ok")},
                                 auto_trust=True)
        self.assertEqual(code, 0, text)
        self.assertNotIn("NOT TRUSTED", text)
        self.assertFalse((self.home / ".claude.json").exists())
        self.assertIs(fleet.DEFAULTS["auto_trust"], False)

    def test_a_cli_without_folder_trust_is_not_failed_for_it(self):
        code, text = self.doctor({"cursor": provider("cursor-agent", login="cursor-agent status", login_ok="Logged in")})
        self.assertEqual(code, 1, text)
        self.assertNotIn("NOT TRUSTED", text)
        self.assertIn("fix: cursor-agent login", text)

    def test_a_disabled_cli_is_not_checked(self):
        code, text = self.doctor({"claude": provider("claude", enabled=False, trust="claude-config",
                                                     login="claude auth status", login_ok='"loggedIn": true')})
        self.assertEqual(code, 0, text)
        self.assertIn("off", text)
        self.assertNotIn("NOT TRUSTED", text)
        self.assertFalse(self.log.exists())


class InitOwnerAction(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.home = self.cache / "home"
        self.home.mkdir()
        bindir = self.home / "bin"
        bindir.mkdir()
        codex = bindir / "codex"
        codex.write_text("#!/bin/sh\nexit 1\n")
        codex.chmod(0o755)
        env = patch.dict(os.environ, {"HOME": str(self.home), "PATH": f"{bindir}:/usr/bin:/bin"})
        env.start()
        self.addCleanup(env.stop)
        self.cfg["providers"]["writer"].update(trust="claude-config", login="echo no", login_ok="Logged in")
        self.cfg["providers"]["reviewer"].update(trust="codex-flag", login="codex login status", login_ok="Logged in",
                                                 bin="codex", default_home=str(self.home / "codex"))

    def test_init_prints_one_owner_action_block_and_still_creates_the_run(self):
        with patch.object(fleet, "trust_claude", side_effect=AssertionError("init wrote trust")):
            out = self.call("init", "demo", "--workspace", "workspace:1", "--review", "off", *self.off_reason("off"))
        self.assertEqual(out.count("owner action needed"), 1)
        self.assertIn("- writer: not trusted: checkout ", out)
        self.assertIn("worktree root ", out)
        self.assertIn(str(self.repo.resolve()), out)
        self.assertIn("fix: set auto_trust = true under [defaults]", out)
        self.assertIn("explicit owner opt-in; the default stays false", out)
        self.assertIn("- writer: not logged in.", out)
        self.assertIn("- reviewer: not logged in. fix: codex login", out)
        self.assertIn("Next: fleet plan demo", out)
        self.assertTrue((self.repo / ".fleet/runs/demo/run.json").exists())
        self.assertFalse((self.home / ".claude.json").exists())
        # Trusted and logged in: the next run prints no second block.
        checkout = str(self.repo.resolve())
        tree = str(fleet.worktree_home(self.cfg).resolve())
        (self.home / ".claude.json").write_text(json.dumps({"projects": {
            checkout: {"hasTrustDialogAccepted": True}, tree: {"hasTrustDialogAccepted": True}}}))
        codex = self.home / "codex"
        codex.mkdir()
        (codex / "config.toml").write_text(
            "\n".join(f'[projects."{path}"]\ntrust_level = "trusted"' for path in (checkout, tree)) + "\n")
        self.cfg["providers"]["writer"]["login"] = "echo Logged in"
        self.cfg["providers"]["reviewer"]["login"] = "echo Logged in"
        again = self.call("init", "other", "--workspace", "workspace:1", "--review", "off", *self.off_reason("off"))
        self.assertNotIn("owner action needed", again)
