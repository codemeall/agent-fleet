"""Launch profiles, the permission ceiling and doctor's flag-drift check (no live agents, cmux or network)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

FAKE_CLI = Path(__file__).resolve().parent / "fixtures" / "fake_cli.py"
SID = "0" * 8 + "-0000-4000-8000-" + "0" * 12


def shipped(user_toml: str = "") -> dict:
    """The shipped config, merged with a user config when one is given."""
    if not user_toml:
        return fleet.load_config(Path("/nonexistent/fleet-config.toml"))
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
        fh.write(user_toml)
    try:
        return fleet.load_config(Path(fh.name))
    finally:
        os.unlink(fh.name)


class Profiles(unittest.TestCase):
    def setUp(self):
        self.cfg = shipped()
        self.p = self.cfg["providers"]

    def test_every_supported_adapter_declares_both_profiles_with_a_level(self):
        for name, p in self.p.items():
            if p.get("placeholder"):
                continue
            with self.subTest(name):
                self.assertIn("{profile}", p["launch"])
                for role in fleet.PROFILES:
                    self.assertIn(p["profiles"][role]["level"], fleet.PERMISSION_LEVELS)
        self.assertEqual(self.cfg["defaults"]["permission_ceiling"], "workspace-write")

    def test_worker_launch_lines_are_unchanged(self):
        """The worker profile reproduces the launch lines shipped before profiles, byte for byte."""
        home = str(Path("~/.claude-co").expanduser())
        expected = {
            "codex": ("gpt-6-sol", "high", "env -u CODEX_HOME codex -m gpt-6-sol -c model_reasoning_effort=high "
                                           "-c check_for_update_on_startup=false -s workspace-write -a on-request go"),
            "cursor": ("grok-4.7-high", None, "cursor-agent --model grok-4.7-high --trust go"),
            "claude": ("opus", "high", "env -u CLAUDE_CONFIG_DIR claude --session-id " + SID + " --model opus --effort high go"),
            "claude-co": ("opus", "high", f"env CLAUDE_CONFIG_DIR={home} claude --model opus --effort high go"),
            "agy": ("gemini-3.8-flash-high", "xhigh", "agy --model gemini-3.8-flash-high --effort xhigh --mode accept-edits -i go"),
            "opencode": ("openrouter/z-ai/glm-5.3", None, "opencode --model openrouter/z-ai/glm-5.3 --prompt go"),
        }
        for name, (model, effort, line) in expected.items():
            with self.subTest(name):
                session = SID if name == "claude" else None
                self.assertEqual(fleet.build_launch(self.p[name], model, effort, "go", session), line)
                self.assertEqual(fleet.build_launch(self.p[name], model, effort, "go", session, profile="worker"), line)

    def test_review_profiles(self):
        """Claude reviewers pin manual mode; the others have no narrower interactive mode that can still write a report."""
        claude = fleet.build_launch(self.p["claude"], "opus", "high", "go", profile="review")
        self.assertEqual(claude, "env -u CLAUDE_CONFIG_DIR claude --model opus --effort high --permission-mode manual go")
        self.assertEqual(fleet.adapter_profile(self.p["claude"], "review")["level"], "read-only")
        self.assertIn("--permission-mode manual", fleet.build_launch(self.p["claude-co"], "opus", "high", "go", profile="review"))
        for name, model, effort in (("codex", "m", "high"), ("cursor", "m", None), ("agy", "m", "high"), ("opencode", "m", None)):
            with self.subTest(name):
                self.assertEqual(fleet.build_launch(self.p[name], model, effort, "go", profile="review"),
                                 fleet.build_launch(self.p[name], model, effort, "go"))
        for name in ("claude", "codex", "cursor", "agy"):
            self.assertEqual(fleet.adapter_profile(self.p[name], "worker")["level"], "workspace-write")
        for role in fleet.PROFILES:
            self.assertEqual(fleet.adapter_profile(self.p["opencode"], role)["level"], "full-access")

    def test_the_strict_fake_accepts_claudes_review_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "run" / "prompts" / "review-01.md"
            prompt.parent.mkdir(parents=True)
            (root / "run" / "reports").mkdir()
            prompt.write_text("# prompt\n")
            (root / "bin").mkdir()
            fake = root / "bin" / "claude"
            fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" claude "$@"\n')
            fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{root / 'bin'}:{os.environ['PATH']}", FAKE_CLI_LOG=str(root),
                       FAKE_CLI_EXPECT=str(root / "expect.json"))
            env.pop("CLAUDE_CONFIG_DIR", None)
            line = fleet.worker_prompt_line(prompt, "review-01")
            for mode, profile, code in (("manual", "review", 0), (None, "worker", 0), (None, "review", 2)):
                with self.subTest(mode=mode, profile=profile):
                    (root / "expect.json").write_text(json.dumps({"model": "opus", "effort": "high", "session": None,
                                                                  "permission_mode": mode, "account_dir": None}))
                    command = fleet.build_launch(self.p["claude"], "opus", "high", line, profile=profile)
                    res = subprocess.run(command, shell=True, env=env, input="/exit\n", text=True,
                                         capture_output=True, timeout=30)
                    self.assertEqual(res.returncode, code, f"{command}\n{res.stdout}{res.stderr}")

    def test_an_owner_launch_without_a_profile_slot_keeps_working(self):
        old = ("{bin} -m {model} -c model_reasoning_effort={effort} -c check_for_update_on_startup=false "
               "-c approvals_reviewer=auto_review -s workspace-write -a on-request {prompt}")
        p = shipped(f'[providers.codex]\nlaunch = "{old}"\n[providers.claude]\n'
                    'launch = "{bin} {session} --model {model} --effort {effort} {prompt}"\n')["providers"]
        line = ("env -u CODEX_HOME codex -m m -c model_reasoning_effort=high -c check_for_update_on_startup=false "
                "-c approvals_reviewer=auto_review -s workspace-write -a on-request go")
        for role in fleet.PROFILES:
            self.assertEqual(fleet.build_launch(p["codex"], "m", "high", "go", profile=role), line)
        # Without the slot the review line is the worker line, so it counts at the worker's level, not read-only.
        self.assertNotIn("--permission-mode", fleet.build_launch(p["claude"], "opus", "high", "go", profile="review"))
        self.assertEqual(fleet.adapter_profile(p["claude"], "review"), {"args": "", "level": "workspace-write"})

    def test_an_owner_can_override_one_profile_field(self):
        p = shipped('[providers.opencode.profiles.worker]\nlevel = "workspace-write"\n')["providers"]["opencode"]
        self.assertEqual(fleet.adapter_profile(p, "worker"), {"args": "", "level": "workspace-write"})
        self.assertEqual(fleet.adapter_profile(p, "review")["level"], "full-access")

    def test_an_adapter_without_profiles_counts_as_workspace_write(self):
        p = {"bin": "x", "launch": "{bin} --model {model} {prompt}"}
        for role in fleet.PROFILES:
            self.assertEqual(fleet.adapter_profile(p, role), fleet.UNDECLARED_PROFILE)
        p = {"bin": "x", "launch": "{bin} {profile} {prompt}", "profiles": {"worker": {"args": "-w", "level": "read-only"}}}
        self.assertEqual(fleet.build_launch(p, "m", None, "go", profile="review"), "x -w go")  # review runs as the worker

    def test_bad_ceiling_or_profile_is_a_config_error(self):
        for toml, words in (('[defaults]\npermission_ceiling = "yolo"\n', "permission_ceiling must be one of"),
                            ('[providers.codex.profiles.review]\nlevel = "sandboxed"\n', "codex.profiles must be"),
                            ('[providers.x]\nbin = "x"\nlaunch = "{bin}"\nprofiles.worker = { args = "-a" }\n',
                             "x.profiles must be"),
                            ('[providers.cursor]\nbin_aliases = "agent"\n', "cursor.bin_aliases")):
            with self.subTest(toml), self.assertRaises(fleet.FleetError) as caught:
                shipped(toml)
            self.assertIn(words, str(caught.exception))

    def test_launch_profile_follows_the_role(self):
        self.assertEqual(fleet.launch_profile({"role": "implementation"}, {"kind": "implement"}), "worker")
        self.assertEqual(fleet.launch_profile({"role": "implementation"}, {}), "worker")
        self.assertEqual(fleet.launch_profile({"role": "implementation"}, {"kind": "investigate"}), "review")
        self.assertEqual(fleet.launch_profile({"role": "review", "review_of": "01"}, None), "review")

    def test_the_ceiling_names_the_adapter_profile_and_ceiling(self):
        why = fleet.ceiling_problem(self.cfg, "opencode", self.p["opencode"], "worker")
        for words in ("opencode's worker profile is full-access", "above permission_ceiling workspace-write",
                      "permission_ceiling", "providers.opencode.profiles.worker.level"):
            self.assertIn(words, why)
        self.assertIsNone(fleet.ceiling_problem(self.cfg, "codex", self.p["codex"], "worker"))
        strict = dict(self.cfg, defaults=dict(self.cfg["defaults"], permission_ceiling="read-only"))
        self.assertIsNotNone(fleet.ceiling_problem(strict, "claude", self.p["claude"], "worker"))
        self.assertIsNone(fleet.ceiling_problem(strict, "claude", self.p["claude"], "review"))
        loose = dict(self.cfg, defaults=dict(self.cfg["defaults"], permission_ceiling="full-access"))
        self.assertIsNone(fleet.ceiling_problem(loose, "opencode", self.p["opencode"], "worker"))
        with self.assertRaises(fleet.FleetError):
            fleet.check_ceiling(self.cfg, "opencode", self.p["opencode"], "review")

    def test_a_reviewer_above_the_ceiling_is_never_picked(self):
        cfg = shipped('[providers.opencode]\nenabled = true\n')
        why, _ = fleet.review_fit(cfg, "auto", None, "opencode", "anthropic")
        self.assertIn("above permission_ceiling", why)
        cfg["defaults"]["permission_ceiling"] = "full-access"
        self.assertIsNone(fleet.review_fit(cfg, "auto", None, "opencode", "anthropic")[0])

    def test_launch_flags_cover_the_line_its_profiles_and_sessions(self):
        self.assertEqual(fleet.launch_flags(self.p["claude"]), ["--model", "--effort", "--session-id", "--resume",
                                                                 "--permission-mode"])
        self.assertEqual(fleet.launch_flags(self.p["codex"]), ["-m", "-c", "-s", "-a"])
        self.assertEqual(fleet.launch_flags(self.p["cursor"]), ["--model", "--trust"])
        self.assertEqual(fleet.launch_flags({"launch": "{bin} --x=1 {prompt}", "session": "--s {id}"}), ["--x"])


class Doctor(unittest.TestCase):
    """Doctor against small fake CLIs on PATH: drift warns, unreadable help is unverified, a missing binary fails."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name)

    def fake(self, name: str, script: str) -> None:
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o755)

    def doctor(self, providers: dict, ceiling: str = "workspace-write") -> tuple[int, str]:
        cfg = {"defaults": dict(fleet.DEFAULTS, permission_ceiling=ceiling), "providers": providers}
        out = io.StringIO()
        with patch.dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}"), \
                patch.object(fleet, "cmux", return_value=""), patch.object(fleet.Tmux, "check", return_value="tmux"), \
                patch.object(fleet, "skill_dirs", return_value={}), patch.object(fleet, "companion_skills", return_value={}), \
                contextlib.redirect_stdout(out):
            code = fleet.cmd_doctor(type("A", (), {"providers": list(providers)})(), cfg)
        return code, out.getvalue()

    def adapter(self, bin_name: str, launch: str, **extra) -> dict:
        return dict({"bin": bin_name, "launch": launch, "enabled": True, "login": "echo ok", "login_ok": "ok",
                     "profiles": {"worker": {"args": "--edits", "level": "workspace-write"},
                                  "review": {"args": "--look", "level": "read-only"}}}, **extra)

    def test_a_flag_missing_from_help_warns_without_failing(self):
        self.fake("fakecli", 'echo "Usage: fakecli [--model M] [--edits]"\n')
        code, text = self.doctor({"x": self.adapter("fakecli", "{bin} --model {model} {profile} {prompt}",
                                                    login="echo ok", login_ok="ok")})
        self.assertEqual(code, 0, text)
        self.assertIn("x             authenticated", text)
        self.assertIn(f"bin {self.bin / 'fakecli'}; profiles worker workspace-write, review read-only; "
                      "ceiling workspace-write", text)
        self.assertIn("WARNING: --look is not in `fakecli --help`", text)
        self.assertNotIn("--model is not", text)
        self.assertNotIn("--edits is not", text)

    def test_a_subcommand_launch_also_reads_the_subcommands_help(self):
        self.fake("fakecli", 'if [ "$1" = exec ]; then echo "exec: --json"; else echo "fakecli: --model exec"; fi\n')
        code, text = self.doctor({"x": self.adapter("fakecli", "{bin} exec --json --model {model} --yolo {prompt}",
                                                    profiles={})})
        self.assertEqual(code, 0, text)
        self.assertIn("WARNING: --yolo is not in `fakecli --help` and `fakecli exec --help`", text)
        self.assertNotIn("--json is not", text)
        self.assertIn("(none declared, so counted as workspace-write)", text)

    def test_unreadable_help_leaves_flags_unverified(self):
        self.fake("nohelp", 'echo "unknown option $1" >&2; exit 2\n')
        self.fake("silent", "exit 0\n")
        code, text = self.doctor({"x": self.adapter("nohelp", "{bin} --model {model} {prompt}"),
                                  "y": self.adapter("silent", "{bin} --model {model} {prompt}")})
        self.assertEqual(code, 0, text)
        self.assertIn("flags unverified: `nohelp --help` exited 2", text)
        self.assertIn("flags unverified: `silent --help` printed nothing", text)
        self.assertIn("(the launch line has no {profile} slot, so every role runs it as written)", text)
        self.assertNotIn("WARNING", text)

    def test_a_missing_binary_still_fails_and_names_an_alias_it_found(self):
        self.fake("agent", 'echo "--model --trust"\n')
        code, text = self.doctor({"cursor": self.adapter("cursor-agent", "{bin} --model {model} {prompt}",
                                                         bin_aliases=["agent"])})
        self.assertEqual(code, 1, text)
        self.assertIn("cursor        MISSING binary 'cursor-agent' (found 'agent'", text)
        code, text = self.doctor({"cursor": self.adapter("cursor-agent", "{bin} {prompt}")})
        self.assertEqual(code, 1)
        self.assertIn("MISSING binary 'cursor-agent'\n", text)

    def test_a_profile_above_the_ceiling_fails_doctor(self):
        self.fake("opencode", 'echo "--model --prompt"\n')
        full = {"worker": {"args": "", "level": "full-access"}, "review": {"args": "", "level": "full-access"}}
        x = self.adapter("opencode", "{bin} --model {model} {profile} --prompt {prompt}", profiles=full)
        code, text = self.doctor({"opencode": x})
        self.assertEqual(code, 1, text)
        self.assertIn("ABOVE CEILING: opencode's worker profile is full-access, above permission_ceiling "
                      "workspace-write", text)
        self.assertIn("flags listed by `opencode --help`", text)
        self.assertEqual(self.doctor({"opencode": x}, ceiling="full-access")[0], 0)

    def test_a_hanging_help_times_out_as_unverified(self):
        self.fake("slow", "exec sleep 2\n")
        with patch.dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}"), patch.object(fleet, "HELP_TIMEOUT_S", 0.5):
            missing, why = fleet.flag_drift({"bin": "slow", "launch": "{bin} --x {prompt}"})
        self.assertIsNone(missing)
        self.assertIn("took over", why)


class LaunchProfiles(RuntimeCase):
    """launch passes each role's profile to the adapter and refuses one above the owner's ceiling."""

    def setUp(self):
        super().setUp()
        for name in ("writer", "reviewer"):
            self.cfg["providers"][name]["launch"] = "{bin} --model {model} --effort {effort} {profile} {prompt}"
            self.cfg["providers"][name]["profiles"] = {"worker": {"args": "--edit", "level": "workspace-write"},
                                                       "review": {"args": "--look", "level": "read-only"}}

    def command(self, ticket: str) -> str:
        return self.state()["workers"][ticket]["command"]

    def test_each_role_launches_its_profile(self):
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"]},
                                                     {"id": "why", "kind": "investigate"}])
        self.launch()
        self.assertIn("--effort high --edit ", self.command("01"))
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.hand_back()
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        self.assertIn("--effort high --look ", self.command("review-01"))
        self.assertNotIn("--edit", self.command("review-01"))
        self.hand_back("review-01")
        self.launch("why")
        self.assertIn("--effort high --look ", self.command("why"))

    def test_launch_refuses_a_profile_above_the_ceiling_before_any_side_effect(self):
        self.cfg["providers"]["writer"]["profiles"]["worker"]["level"] = "full-access"
        self.setup_run()
        out = self.launch(expected=1)
        self.assertIn("writer's worker profile is full-access, above permission_ceiling workspace-write", out)
        self.assertNotIn("01", self.state()["workers"])
        self.assertFalse((self.repo / ".fleet/runs/demo/snapshots/01.json").exists())
        self.assertFalse([c for c in self.cmux_calls if c[0] in ("new-surface", "new-split")])
        self.cfg["defaults"]["permission_ceiling"] = "full-access"  # the owner's decision, not the lead's
        self.launch()
        self.assertIn("--edit", self.command("01"))


if __name__ == "__main__":
    unittest.main()
