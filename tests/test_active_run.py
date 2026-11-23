"""The one-active-run refusal names the blocking run and the commands that clear it. It does not rewrite the run."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


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
        self.assertIn("ask the owner before stopping a worker in another run", text)
        self.assertIn("\n01 running\nfleet stop old 01\n", text)
        self.assertIn("\n04 launching\nfleet stop old 04\n", text)
        self.assertNotIn("fleet recover old 01", text)
        self.assertNotIn("fleet recover old 04", text)
        self.assertIn("\n02 stop-failed\nfleet recover old 02 --evidence EVIDENCE_FILE", text)
        self.assertIn("\n05 unknown\nfleet recover old 05 --evidence EVIDENCE_FILE", text)
        self.assertNotIn("fleet stop old 02", text)
        self.assertNotIn("fleet stop old 05", text)
        self.assertNotIn("<file>", text)
        self.assertNotIn("03 ", text)
        self.assertNotIn("exited", text)
        self.assertNotIn("06 ", text)
        self.assertNotIn("recovered", text)
        self.assertEqual(path.read_bytes(), before)

    def test_an_exited_run_and_the_current_run_do_not_block(self):
        self.write("done", {"01": {"id": "01", "state": "exited"}})
        self.write("new", {"01": {"id": "01", "state": "running"}})
        fleet.ensure_other_runs_idle(self.repo, "new")

    def test_ids_are_quoted_and_a_known_pid_is_named(self):
        self.write("old run", {
            "a;b": {"id": "a;b", "state": "running"},
            "02": {"id": "02", "state": "unknown", "process": {"child_pid": 4242}},
        })
        with self.assertRaises(fleet.FleetError) as raised:
            fleet.ensure_other_runs_idle(self.repo, "new")
        text = str(raised.exception)
        self.assertIn("fleet stop 'old run' 'a;b'", text)
        self.assertIn("fleet recover 'old run' 02 --evidence EVIDENCE_FILE", text)
        self.assertIn("pid 4242", text)
        self.assertIn("peek or ps", text)
        self.assertIn("say how in the evidence file", text)


class ActiveRunCli(RuntimeCase):
    def plant(self, workers: dict):
        self.setup_run()
        path = self.repo / ".fleet" / "runs" / "demo" / "run.json"
        state = json.loads(path.read_text())
        state["workers"] = workers
        path.write_text(json.dumps(state, indent=2) + "\n")
        return path

    def refuse(self) -> tuple[str, bytes, Path]:
        path = self.repo / ".fleet" / "runs" / "demo" / "run.json"
        before = path.read_bytes()
        out = self.call("init", "other", "--backend", "tmux", "--review", "off", *self.off_reason("off"), expected=1)
        return out, before, path

    def test_a_running_worker_refuses_init_and_writes_nothing(self):
        self.plant({"01": {"id": "01", "state": "running"}})
        out, before, path = self.refuse()
        self.assertIn("ask the owner before stopping a worker in another run", out)
        self.assertIn("fleet stop demo 01", out)
        self.assertNotIn("fleet recover", out)
        self.assertNotIn("<file>", out)
        self.assertFalse((self.repo / ".fleet" / "runs" / "other").exists())
        self.assertEqual(path.read_bytes(), before)

    def test_a_stop_failed_worker_ends_in_recover(self):
        self.plant({"02": {"id": "02", "state": "stop-failed", "process": {"child_pid": 4242}}})
        out, before, path = self.refuse()
        last = out.rstrip().splitlines()[-1]
        self.assertIn("fleet recover demo 02 --evidence EVIDENCE_FILE", last)
        self.assertIn("pid 4242", last)
        self.assertNotIn("fleet stop", out)
        self.assertNotIn("<file>", out)
        self.assertFalse((self.repo / ".fleet" / "runs" / "other").exists())
        self.assertEqual(path.read_bytes(), before)


class ProcessGone(unittest.TestCase):
    """reap_gone's process test: every recorded pid dead (or reused), in this pid namespace, and at least one."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve()
        self.exits = self.repo / ".fleet" / "runs" / "r" / "exits"
        self.exits.mkdir(parents=True)
        self.state = {"run": "r", "workers": {}}
        self.w = {"id": "01", "token": "tok", "exit_file": str(self.exits / "01.tok.json")}

    def record(self, suffix=".started.json", **fields):
        data = {"token": "tok", "pidns": fleet.pid_namespace(), **fields}
        (self.exits / f"01.tok{suffix}").write_text(json.dumps(data))

    def dead_pid(self) -> int:
        proc = subprocess.Popen(["true"])
        proc.wait()
        return proc.pid

    def gone(self) -> bool:
        return fleet.process_gone(self.repo, self.state, self.w)

    def test_no_recorded_process_is_never_gone(self):
        self.assertFalse(self.gone())
        self.record(wrapper_pid=None)
        self.assertFalse(self.gone())

    def test_a_live_pid_is_not_gone(self):
        self.record(wrapper_pid=os.getpid(), wrapper_start=fleet.process_start(os.getpid()), child_pid=self.dead_pid())
        self.assertFalse(self.gone())

    def test_dead_pids_are_gone(self):
        self.record(wrapper_pid=self.dead_pid(), child_pid=self.dead_pid())
        self.assertTrue(self.gone())

    def test_a_runner_still_at_its_gate_counts(self):
        self.record(".runner.json", wrapper_pid=os.getpid(), wrapper_start=fleet.process_start(os.getpid()))
        self.record(wrapper_pid=self.dead_pid(), child_pid=self.dead_pid())
        self.assertFalse(self.gone())

    def test_a_reused_pid_is_gone(self):
        if fleet.process_start(os.getpid()) is None:
            self.skipTest("no process start times here")
        self.record(wrapper_pid=os.getpid(), wrapper_start="not-when-it-started")
        self.assertTrue(self.gone())

    def test_another_launch_or_pid_namespace_says_nothing(self):
        self.record(wrapper_pid=self.dead_pid(), token="other")
        self.assertFalse(self.gone())
        self.record(wrapper_pid=self.dead_pid(), pidns="pid:[1]" if fleet.pid_namespace() != "pid:[1]" else "x")
        self.assertFalse(self.gone())
