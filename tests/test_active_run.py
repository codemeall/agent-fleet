"""The one-active-run refusal names the blocking run and the commands that clear it. It does not rewrite the run."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import fleet  # noqa: E402


class ActiveRunRefusal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / ".fleet" / "runs").mkdir(parents=True)

    def write(self, name: str, workers: dict) -> Path:
        path = self.repo / ".fleet" / "runs" / name / "run.json"
        path.parent.mkdir()
        path.write_text(json.dumps({"schema": 2, "run": name, "workers": workers, "tickets": {}}))
        return path

    def test_refusal_lists_each_live_worker_and_the_commands(self):
        path = self.write("old", {
            "01": {"id": "01", "state": "running"},
            "02": {"id": "02", "state": "stop-failed"},
            "03": {"id": "03", "state": "exited"},
            "04": {"id": "04", "state": "launching"},
            "05": {"id": "05", "state": "unknown"},
            "06": {"id": "06", "state": "recovered"},
        })
        before = path.read_bytes()
        with self.assertRaises(fleet.FleetError) as raised:
            fleet.ensure_other_runs_idle(self.repo, "new")
        text = str(raised.exception)
        self.assertIn("run old has workers with unconfirmed exits; use one active run per checkout", text)
        for wid, state in (("01", "running"), ("02", "stop-failed"), ("04", "launching"), ("05", "unknown")):
            self.assertIn(f"\n{wid} {state}\nfleet stop old {wid}\nfleet recover old {wid} --evidence <file>", text)
        self.assertNotIn("03 ", text)
        self.assertNotIn("exited", text)
        self.assertNotIn("06 ", text)
        self.assertNotIn("recovered", text)
        self.assertEqual(path.read_bytes(), before)

    def test_an_exited_run_and_the_current_run_do_not_block(self):
        self.write("done", {"01": {"id": "01", "state": "exited"}})
        self.write("new", {"01": {"id": "01", "state": "running"}})
        fleet.ensure_other_runs_idle(self.repo, "new")
