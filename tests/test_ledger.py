"""The run ledger, its caps and fleet cost: one entry per worker launch, never overwritten, with wall time from
the exit receipt and tokens from a Claude worker's own session log, or unknown."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

REAL_LOAD_CONFIG = fleet.load_config  # RuntimeCase patches it per test


class LedgerCase(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.claude_home = Path(tempfile.mkdtemp(dir=self.temp.name))

    def edit_state(self, change):
        f = self.repo / ".fleet/runs/demo/run.json"
        state = json.loads(f.read_text())
        change(state)
        fleet.save_run(self.repo, "demo", state)

    def age(self, seconds, index=-1):
        """Move a ledger entry's start into the past, as if the worker launched that long ago."""
        def back(state):
            state["ledger"][index]["start"] -= seconds
        self.edit_state(back)

    def exit_at(self, ticket, when):
        self.receipt(ticket)
        os.utime(self.state()["workers"][ticket]["exit_file"], (when, when))
        self.call("status", "demo")

    def claude_writer(self):
        """The writer adapter as a Claude account: named sessions, logs under its own CLAUDE_CONFIG_DIR."""
        self.cfg["providers"]["writer"].update(
            launch="{bin} {session} --model {model} --effort {effort} {prompt}", session="--session-id {id}",
            resume="--resume {id}", account_env="CLAUDE_CONFIG_DIR", env={"CLAUDE_CONFIG_DIR": str(self.claude_home)})

    def session_log(self, session, lines):
        log = self.claude_home / "projects" / "-tmp-repo" / f"{session}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")

    @staticmethod
    def call_line(mid, at, inp, out, cache_read=0, cache_write=0):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))
        return {"type": "assistant", "timestamp": stamp,
                "message": {"id": mid, "usage": {"input_tokens": inp, "output_tokens": out,
                                                 "cache_read_input_tokens": cache_read,
                                                 "cache_creation_input_tokens": cache_write}}}


class Ledger(LedgerCase):
    def test_relaunch_and_resume_append_entries_and_never_overwrite_one(self):
        self.claude_writer()
        self.setup_run()
        self.launch()
        first = self.state()["workers"]["01"]
        self.receipt(returncode=1)
        self.call("status", "demo")
        self.call("launch", "demo", "01", "writer", "--resume")
        self.receipt(returncode=1)
        self.call("status", "demo")
        self.launch()                                       # a fresh relaunch after the resumed one
        ledger = self.state()["ledger"]
        self.assertEqual([e["resume"] for e in ledger], [False, True, False])
        self.assertEqual(len({e["token"] for e in ledger}), 3)
        self.assertEqual(ledger[0]["token"], first["token"])
        self.assertEqual(ledger[0]["session"], ledger[1]["session"])
        self.assertNotEqual(ledger[1]["session"], ledger[2]["session"])
        for e in ledger[:2]:
            self.assertEqual(e["end_source"], "receipt")
            self.assertIsNotNone(e["wall_s"])
        self.assertIsNone(ledger[2]["end"])                 # still running
        self.assertEqual({(e["ticket"], e["role"], e["provider"], e["model"], e["effort"], e["tier"]) for e in ledger},
                         {("01", "writer", "writer", "writer-model", "high", "standard")})

    def test_wall_time_runs_from_launch_to_the_receipt_and_a_recover(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.cfg["defaults"]["max_parallel"] = 2
        self.launch()
        start = self.state()["ledger"][0]["start"]
        self.exit_at("01", start + 600)
        self.call("status", "demo")                         # a later reconcile does not stretch it
        self.assertEqual(self.state()["ledger"][0]["wall_s"], 600)
        self.launch("02")
        self.age(120)
        self.call("recover", "demo", "02", "--evidence", str(self.evidence))
        e = self.state()["ledger"][1]
        self.assertEqual(e["end_source"], "recover")
        self.assertAlmostEqual(e["wall_s"], 120, delta=5)

    def test_a_run_from_before_the_ledger_gets_its_workers_entries(self):
        self.setup_run()
        self.launch()
        self.receipt()
        self.edit_state(lambda s: s.pop("ledger"))
        out = self.call("cost", "demo")
        self.assertIn("01           1 worker, wall ", out)
        self.assertTrue(self.state()["ledger"][0]["backfilled"])

    def test_record_usage_takes_a_cli_report_over_the_log(self):
        self.setup_run()
        self.launch()
        state = self.state()
        fleet.record_usage(state, "01", {"input_tokens": 1200, "output_tokens": 300, "cache_read_input_tokens": 0})
        self.assertEqual(state["ledger"][-1]["tokens"], {"input": 1200, "output": 300, "cache_read": 0})
        self.assertEqual(state["ledger"][-1]["tokens_source"], "reported")
        with self.assertRaises(fleet.FleetError):
            fleet.record_usage(state, "01", {"duration_ms": 10})


class Tokens(LedgerCase):
    def test_tokens_come_from_the_workers_own_session_log(self):
        self.claude_writer()
        self.setup_run()
        self.launch()
        w = self.state()["workers"]["01"]
        start = w["launched_at"]
        self.session_log(w["session"], [
            self.call_line("m1", start + 10, 100, 20, cache_read=5000, cache_write=700),
            self.call_line("m1", start + 11, 100, 20, cache_read=5000, cache_write=700),   # same call, next block
            self.call_line("m2", start + 30, 50, 400, cache_read=6000),
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "usage"}},
        ])
        self.exit_at("01", start + 60)
        self.call("cost", "demo")
        e = self.state()["ledger"][0]
        self.assertEqual(e["tokens"], {"input": 150, "output": 420, "cache_read": 11000, "cache_write": 700})
        self.assertEqual(e["tokens_source"], "session log")

    def test_a_resumed_launch_counts_only_its_own_calls(self):
        self.claude_writer()
        self.setup_run()
        self.launch()
        self.age(3600)                                      # the first launch was an hour ago
        w, start = self.state()["workers"]["01"], self.state()["ledger"][0]["start"]
        self.session_log(w["session"], [self.call_line("m1", start + 5, 100, 10)])
        self.exit_at("01", start + 20)
        self.call("launch", "demo", "01", "writer", "--resume")
        w2 = self.state()["workers"]["01"]
        self.session_log(w["session"], [self.call_line("m2", w2["launched_at"] + 5, 7, 3)])
        self.exit_at("01", w2["launched_at"] + 20)
        self.call("cost", "demo")
        first, resumed = self.state()["ledger"]
        self.assertEqual(first["tokens"], {"input": 100, "output": 10, "cache_read": 0, "cache_write": 0})
        self.assertEqual(resumed["tokens"], {"input": 7, "output": 3, "cache_read": 0, "cache_write": 0})

    def test_tokens_are_unknown_without_a_log_or_a_session(self):
        self.claude_writer()
        self.setup_run()
        self.launch()                                       # a Claude worker whose log never appeared
        self.receipt()
        self.assertIn("tokens unknown", self.call("cost", "demo"))
        self.assertIsNone(self.state()["ledger"][0]["tokens"])
        self.cfg["providers"]["writer"] = dict(self.cfg["providers"]["reviewer"], family="openai")
        for tier in self.cfg["providers"]["writer"]["tiers"].values():
            tier.update(model="writer-model", family="openai")
        self.launch()                                       # a CLI with no session: no log Fleet can read
        self.receipt()
        out = self.call("cost", "demo")
        self.assertIn("01           2 workers, wall ", out)
        self.assertIn("tokens unknown", out)
        self.assertNotIn("in 0", out)


    def log_event(self, ticket, **fields):
        """An event the worker's Stop hook or notify program appended, for its current launch."""
        events = self.repo / ".fleet/runs/demo/events" / f"{ticket}.jsonl"
        events.parent.mkdir(exist_ok=True)
        with events.open("a") as fh:
            fh.write(json.dumps({"event": "turn-ended", "token": self.state()["workers"][ticket]["token"],
                                 "at": time.time(), **fields}) + "\n")

    def test_the_transcript_a_stop_hook_named_counts_when_the_session_log_is_missing(self):
        self.claude_writer()
        self.setup_run()
        self.launch()
        start = self.state()["workers"]["01"]["launched_at"]
        elsewhere = Path(tempfile.mkdtemp(dir=self.temp.name)) / "renamed.jsonl"
        elsewhere.write_text(json.dumps(self.call_line("m1", start + 5, 40, 9, cache_read=300)) + "\n")
        self.log_event("01", transcript=str(elsewhere))
        self.exit_at("01", start + 30)
        self.call("cost", "demo")
        e = self.state()["ledger"][0]
        self.assertEqual(e["tokens"], {"input": 40, "output": 9, "cache_read": 300, "cache_write": 0})
        self.assertEqual(e["tokens_source"], "session log")

    @staticmethod
    def token_count(at, inp, cached, out):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))
        return {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                                  "output_tokens": out, "reasoning_output_tokens": 1, "total_tokens": inp + out}}}}

    def test_a_codex_workers_tokens_come_from_its_rollout(self):
        self.setup_run()
        self.launch()                                       # no session: Codex names its own thread
        start = self.state()["workers"]["01"]["launched_at"]
        rollout = Path(tempfile.mkdtemp(dir=self.temp.name)) / "rollout-2026-11-11T13-57-40-t1.jsonl"
        lines = [self.token_count(start - 600, 1000, 800, 50),   # an earlier launch on the same thread
                 {"timestamp": "2026-11-11T08:28:00Z", "type": "event_msg", "payload": {"type": "agent_message"}},
                 self.token_count(start + 5, 31000, 28000, 300),
                 self.token_count(start + 20, 128075, 105856, 1384),
                 self.token_count(start + 900, 999999, 0, 9)]  # after this launch ended
        rollout.write_text("".join(json.dumps(x) + "\n" for x in lines) + "{cut off")
        self.log_event("01", rollout=str(rollout))
        self.exit_at("01", start + 30)
        out = self.call("cost", "demo")
        e = self.state()["ledger"][0]
        self.assertEqual(e["tokens"], {"input": 22019, "output": 1334, "cache_read": 105056})
        self.assertEqual(e["tokens_source"], "rollout")
        self.assertNotIn("tokens unknown", out)


class Cost(LedgerCase):
    def test_cost_prints_tickets_totals_and_caps_as_text_and_json(self):
        self.claude_writer()
        self.cfg["defaults"].update(max_workers=5, max_wall_minutes=0, max_parallel=2)
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        self.launch()
        w = self.state()["workers"]["01"]
        self.session_log(w["session"], [self.call_line("m1", w["launched_at"] + 5, 1500, 250, cache_read=120000)])
        self.exit_at("01", w["launched_at"] + 300)
        self.launch("02")
        self.age(90)
        out = self.call("cost", "demo")
        self.assertIn("run demo cost, per ticket", out)
        self.assertIn("  01           1 worker, wall 5.0m, tokens in 1.5K out 250 cache-read 120K cache-write 0", out)
        self.assertRegex(out, r"  02           1 worker, 1 running, wall 1\.\dm, tokens unknown")
        self.assertRegex(out, r"total: 2 workers, 1 running, wall 6\.\dm, tokens in 1\.5K out 250 cache-read 120K "
                              r"cache-write 0 \(unknown for 1 of 2 workers\)")
        self.assertRegex(out, r"caps: max_workers 2/5, max_wall_minutes 6\.\dm \(no cap\)")
        data = json.loads(self.call("cost", "demo", "--json"))
        self.assertEqual(data["tickets"]["01"]["tokens"],
                         {"input": 1500, "output": 250, "cache_read": 120000, "cache_write": 0})
        self.assertEqual(data["tickets"]["01"]["wall_s"], 300)
        self.assertIsNone(data["tickets"]["02"]["tokens"])
        self.assertEqual(data["tickets"]["02"]["running"], 1)
        self.assertEqual(data["totals"]["tokens_unknown"], 1)
        self.assertEqual((data["caps"]["workers"], data["caps"]["max_workers"], data["caps"]["max_wall_minutes"]),
                         (2, 5, 0))
        self.assertEqual(len(data["ledger"]), 2)

    def test_cost_before_any_launch_and_resume_and_status_lines(self):
        self.cfg["defaults"]["max_wall_minutes"] = 90
        self.setup_run()
        self.assertIn("(no worker launched yet)", self.call("cost", "demo"))
        self.launch()
        self.age(300)
        out = self.call("resume", "demo")
        self.assertRegex(out, r"cost: 1 worker, 1 running, wall 5\.\dm, tokens unknown  \(fleet cost demo per ticket\)")
        self.assertRegex(out, r"caps: max_workers 1 \(no cap\), max_wall_minutes 5\.\dm/90")
        full, _ = json.JSONDecoder().raw_decode(self.call("resume", "demo", "--json"))
        self.assertEqual(full["cost"]["caps"]["max_wall_minutes"], 90)
        self.assertRegex(self.call("status", "demo"), r"01 .*running .* ran 5\.\dm")


class Caps(LedgerCase):
    def two_tickets(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])

    def test_worker_cap_refuses_new_launches_and_leaves_running_workers(self):
        self.cfg["defaults"]["max_workers"] = 2
        self.two_tickets()
        self.launch()
        self.receipt()
        self.call("status", "demo")
        self.launch()                                       # a relaunch counts as a launch
        out = self.launch("02", expected=1)
        self.assertIn("reached its max_workers cap: 2 of 2 worker launches used", out)
        self.assertIn("running workers continue", out)
        self.assertIn("defaults.max_workers", out)
        self.assertIn("fleet init <new-run>", out)
        self.assertIn("max_workers cap", self.call("launch", "demo", "--ready", expected=1))
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        self.assertNotIn("02", self.state()["workers"])
        self.assertEqual(len(self.state()["ledger"]), 2)

    def test_wall_cap_counts_running_workers_and_refuses_past_it(self):
        self.cfg["defaults"]["max_wall_minutes"] = 30
        self.two_tickets()
        self.launch()
        self.age(20 * 60)
        self.launch("02")                                   # 20 of 30 minutes: still room
        self.age(11 * 60, index=1)
        out = self.call("launch", "demo", "--ready", expected=1)
        self.assertIn("reached its max_wall_minutes cap: 31m of 30 minutes of worker wall time used", out)
        self.assertEqual([w["state"] for w in self.state()["workers"].values()], ["running", "running"])

    def test_wall_cap_after_exits_uses_the_receipt_times(self):
        self.cfg["defaults"]["max_wall_minutes"] = 30
        self.two_tickets()
        self.launch()
        start = self.state()["ledger"][0]["start"]
        self.age(3600)
        self.exit_at("01", start)                           # ran an hour, ended now
        self.assertIn("60m of 30 minutes", self.launch("02", expected=1))

    def test_review_is_refused_at_the_cap_before_the_writer_stops(self):
        self.cfg["defaults"]["max_workers"] = 1
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented\n")
        self.report()
        out = self.call("review", "demo", "01", expected=1)
        self.assertIn("max_workers cap", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")

    def test_no_cap_when_zero_or_absent(self):
        self.cfg["defaults"].update(max_workers=0, max_wall_minutes=0)
        self.two_tickets()
        for _ in range(3):
            self.launch()
            self.age(10 * 3600)
            self.receipt()
            self.call("status", "demo")
        self.edit_state(lambda s: [s["options"].pop(k) for k in fleet.CAP_KEYS])   # a run from before the caps
        self.launch()
        self.launch("02")
        self.assertEqual(len(self.state()["ledger"]), 5)

    def test_config_validates_the_caps(self):
        with tempfile.TemporaryDirectory() as d:
            user = Path(d) / "config.toml"
            for bad in ("-1", "1.5", "true", '"10"'):
                user.write_text(f"[defaults]\nmax_workers = {bad}\n")
                with self.assertRaisesRegex(fleet.FleetError, "defaults.max_workers must be a whole number"):
                    REAL_LOAD_CONFIG(user)
            user.write_text("[defaults]\nmax_wall_minutes = 120\n")
            cfg = REAL_LOAD_CONFIG(user)
            self.assertEqual((cfg["defaults"]["max_wall_minutes"], cfg["defaults"]["max_workers"]), (120, 0))

    def test_init_saves_the_caps_with_the_run(self):
        self.cfg["defaults"].update(max_workers=7, max_wall_minutes=45)
        self.setup_run()
        self.cfg["defaults"].update(max_workers=1)          # a later config change applies to new runs only
        self.assertEqual((self.state()["options"]["max_workers"], self.state()["options"]["max_wall_minutes"]), (7, 45))
        self.launch()
        self.receipt()
        self.call("status", "demo")
        self.launch()


if __name__ == "__main__":
    import unittest
    unittest.main()
