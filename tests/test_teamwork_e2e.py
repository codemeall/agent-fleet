"""Teamwork release 1 end to end: one run through fleet in headless tmux, with scripted fake workers.

Two fake providers of different families (vendora: openai, vendorb: anthropic) run tests/fixtures/
scripted_worker.py, which edits files and writes reports as tests/test_teamwork_e2e.py scripts it. The run covers
the scope check, the inbox (send --changes, keys), grant, gate, review, accept and the hand-down to a dependent,
and ends with the git index untouched. No real CLI, account or network is used.
"""
import json
import shlex
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from fleet_harness import FIXTURES, HAS_TMUX, FleetCase  # noqa: E402

APPENDIX = "- long notes the dependent should not get"
PY = shlex.quote(sys.executable)
API_CHECK = f"{PY} tests/test_api.py"
UI_CHECK = f"{PY} -c \"import sys; sys.path.insert(0, 'src'); import ui\""


def header(files: list[str], checks: list[str]) -> str:
    """The report template's `fleet` block, as a worker that ran its checks writes it."""
    return ("```fleet\n" + json.dumps({"files_changed": files, "checks": [{"run": c, "exit": 0} for c in checks],
                                       "criteria": [], "findings": [], "questions": []}) + "\n```\n\n")


WORKERS = {
    "api": {
        # A stray README.md edit, outside every ticket's files, rides along with the first hand-back.
        "edit": {"src/api.py": "def get():\n    return 1\n", "README.md": "stray edit outside the ticket\n"},
        "report": "## Work or review performed\n- src/api.py: added get()\n\n## Acceptance criteria\n"
                  f"- [x] get() returns 1: tests/test_api.py\n\n## Appendix\n{APPENDIX}",
        "on_message": [
            {"edit": {"tests/test_api.py": "import sys; sys.path.insert(0, 'src')\nimport api\nassert api.get() == 1\n"},
             "report": "## Work or review performed\n- src/api.py: get()\n- tests/test_api.py: covers get()\n\n"
                       "## Acceptance criteria\n- [x] get() returns 1: tests/test_api.py passes\n\n"
                       f"## Appendix\n{APPENDIX}"},
            {"edit": {"src/util.py": "def helper():\n    return 'ok'\n"},
             "report": header(["src/api.py", "tests/test_api.py", "src/util.py"], [API_CHECK])
                       + "## Work or review performed\n- src/api.py: get()\n- tests/test_api.py: covers get()\n"
                       "- src/util.py: helper() (granted)\n\n## Acceptance criteria\n"
                       f"- [x] get() returns 1: tests/test_api.py passes\n\n## Appendix\n{APPENDIX}"},
        ],
    },
    "review-api": {"report": header([], []) + "## Work or review performed\n- Read the captured diff.\n\n"
                                              "## Findings\n- No actionable findings."},
    "ui": {"edit": {"src/ui.py": "import api\nprint(api.get())\n"},
           "report": header(["src/ui.py"], [UI_CHECK]) + "## Work or review performed\n- src/ui.py: shows get()"},
}
FIXES = "Changes requested:\n1) add tests/test_api.py covering get()\n2) set your report back to needs-verification\n"


def toml(value: str) -> str:
    return json.dumps(value)  # a TOML basic string, for the paths and commands used here


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class TeamworkRelease1(FleetCase):
    def setUp(self):
        super().setUp()
        self.log = self.root / "worker-log"
        self.log.mkdir()
        behaviour = self.root / "behaviour.json"
        behaviour.write_text(json.dumps(WORKERS))
        worker = " ".join(shlex.quote(p) for p in (sys.executable, str(FIXTURES / "scripted_worker.py")))
        env = f"env = {{ FAKE_PLAN = {toml(str(behaviour))}, FAKE_LOG = {toml(str(self.log))} }}"
        providers = "".join(
            f'\n[providers.{name}]\nenabled = true\nbin = {toml(worker)}\nlaunch = "{{bin}} --model {{model}} {{prompt}}"\n'
            f'quit = ["/quit", "enter"]\nfamily = "{family}"\n{env}\nmax = {cap}\n'
            + "".join(f'tiers.{tier} = {{ model = "{name[-1]}-{size}" }}\n'
                      for tier, size in (("heavy", "big"), ("standard", "mid"), ("light", "small"), ("review", "big")))
            for name, family, cap in (("vendora", "openai", 2), ("vendorb", "anthropic", 1)))
        self.write_config('prefer = ["vendora", "vendorb"]\nmax_parallel = 2\nreview = "cross-heavy"\n' + providers)
        (self.repo / "README.md").write_text("# Demo\n")
        (self.repo / "src").mkdir()
        (self.repo / "src/__init__.py").write_text("")
        (self.repo / "tests").mkdir()
        self.git("add", "-A")
        self.git("commit", "-qm", "demo")
        issues = self.repo / ".scratch/feat/issues"
        issues.mkdir(parents=True)
        (issues / "api.md").write_text("# api: add get()\n")
        (issues / "ui.md").write_text("# ui: show get()\n")
        self.plan = self.repo / ".scratch/feat/plan.json"
        self.plan.write_text(json.dumps({"tickets": [
            {"id": "api", "ticket": ".scratch/feat/issues/api.md", "files": ["src/api.py", "tests/test_api.py"],
             "provider": "vendora", "tier": "heavy", "context": "Spec: get() returns 1.",
             "checks": ["Read src/api.py", {"run": API_CHECK, "timeout": 30}]},
            {"id": "ui", "ticket": ".scratch/feat/issues/ui.md", "files": ["src/ui.py"], "blockers": ["api"],
             "provider": "vendora", "tier": "standard", "context": "Use api.get().",
             "checks": [{"run": UI_CHECK, "timeout": 30}]},
        ], "decisions": ["Plan-time decision: keep it tiny."]}))
        self.fixes = self.root / "fixes.md"  # the lead's own files live outside the checkout
        self.fixes.write_text(FIXES)
        self.notes = self.root / "notes.md"
        self.notes.write_text("Lead: read the api report and diff; criteria met; the review found nothing.\n")

    def state(self) -> dict:
        return json.loads((self.repo / ".fleet/runs/e2e/run.json").read_text())

    def received(self, ticket: str) -> list[str]:
        f = self.log / f"received-{ticket}.log"
        return f.read_text().splitlines(keepends=True) if f.exists() else []

    def test_two_tickets_from_plan_to_accept(self):
        index = self.git("ls-files", "--stage")
        self.fleet("init", "e2e")
        self.assertIn("saved 2 tickets", self.fleet("plan", "e2e", "--file", str(self.plan)))

        out = self.fleet("launch", "e2e", "--ready")
        self.assertRegex(out, r"LAUNCHED api vendora:a-big heavy @\d+")
        self.assertIn("SKIPPED ui: blocked by api", out)

        # The worker hands back with a stray edit: wait names both.
        out = self.wait_for("e2e", r"^REPORT api\b")
        self.assertRegex(out, r"(?m)^SCOPE README\.md \(changed\) is outside every ticket's files")

        # A multi-line fix list: the terminal gets one pointer line, the inbox the whole text.
        out = self.fleet("send", "e2e", "api", "--changes", "--file", str(self.fixes), "--key", "fix-1")
        inbox = self.repo / ".fleet/runs/e2e/inbox/api.md"
        self.assertIn("sent message 1 to api", out)
        self.wait_for("e2e", r"^REPORT api\b")
        self.assertIn(FIXES.strip(), inbox.read_text())
        self.assertEqual(self.received("api"), [f'Lead message 1: read "## Message 1" in {inbox}, then continue.\n'])
        out = self.fleet("send", "e2e", "api", "--changes", "--file", str(self.fixes), "--key", "fix-1")
        self.assertIn("api: key fix-1 was already sent as message 1; nothing sent", out)
        self.assertEqual(len(self.received("api")), 1)
        self.assertEqual(inbox.read_text().count("## Message "), 1)

        # The live worker needs one more file.
        out = self.fleet("grant", "e2e", "api", "src/util.py", "--reason", "helper the API needs")
        self.assertIn("api: granted src/util.py", out)
        self.assertIn("sent message 2 to api", out)
        self.wait_for("e2e", r"^REPORT api\b")
        self.assertTrue((self.repo / "src/util.py").exists())

        # The stray edit blocks review before anything stops.
        out = self.fleet("review", "e2e", "api", "--settle", "0", expected=1)
        self.assertIn("README.md (changed)", out)
        self.assertIn("Nothing was stopped", out)
        self.assertEqual(self.state()["workers"]["api"]["state"], "running")

        self.git("checkout", "-q", "README.md")  # the owner reverts it

        self.assertRegex(self.fleet("gate", "e2e", "api"), r"GATE api 1/1 passed, scope ok")
        out = self.fleet("review", "e2e", "api", "--settle", "0")
        self.assertRegex(out, r"api: exited \(@\d+\)")
        self.assertRegex(out, r"DIFF \S+api\.diff \(3 files\)")
        self.assertRegex(out, r"REVIEW review-api vendorb:b-big family=anthropic on @\d+")
        self.wait_for("e2e", r"^REPORT review-api\b")

        out = self.fleet("accept", "e2e", "api", "--gate", "--evidence", str(self.notes), "--launch-ready")
        for line in ("review-api: verified", "api: verified", "UNBLOCKED ui"):
            self.assertIn(line, out)
        self.assertRegex(out, r"GATE api 1/1 passed")
        self.assertRegex(out, r"LAUNCHED ui vendora:a-mid standard @\d+")

        # ui's prompt hands down what api delivered: its changed files and report summary, not its appendix.
        prompt = (self.repo / ".fleet/runs/e2e/prompts/ui.md").read_text()
        handdown = prompt[prompt.index("### api: verified"):]
        self.assertIn("Changed files: src/api.py (created), src/util.py (created), tests/test_api.py (created)",
                      handdown)
        self.assertIn("> - src/util.py: helper() (granted)", handdown)
        self.assertIn("> - [x] get() returns 1: tests/test_api.py passes", handdown)
        self.assertIn("Granted src/util.py to api: helper the API needs", handdown)
        self.assertNotIn(APPENDIX.lstrip("- "), prompt)

        self.wait_for("e2e", r"^REPORT ui\b")
        self.assertEqual((self.log / "prompt-ui.md").read_text(), prompt)  # what the ui worker was given
        out = self.fleet("accept", "e2e", "ui", "--gate", "--evidence", str(self.notes))
        self.assertIn("ui: verified", out)
        self.assertIn("UNBLOCKED none", out)
        self.assertIn("OK: captured HEAD and index states match the run baseline", self.fleet("check", "e2e"))
        self.assertEqual({i: t["status"] for i, t in self.state()["tickets"].items()},
                         {"api": "verified", "ui": "verified"})
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.git("ls-files", "--stage"), index)


if __name__ == "__main__":
    unittest.main()
