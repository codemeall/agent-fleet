"""TW20 (#49): fleet usage, the read-only usage report. Rework and first-pass numbers from a scripted history
(repairs, a gate round, a findings round, a usage-limit resume and a drop), grouping, --all, --json, unknown
tokens kept unknown, and runs from before the ledger and the report header."""
import json
import unittest
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

HIGH = {"severity": "high", "file": "owned.txt:1", "confidence": "confirmed"}
LOW = {"severity": "low", "file": "owned.txt:2", "confidence": "suspected"}
FIXED = [{"finding": f, "action": "fixed", "evidence": "owned.txt keeps the line; checked by hand"} for f in ("F1", "F2")]
RESUMABLE = "{bin} {session} --model {model} --effort {effort} {prompt}"


class UsageCase(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.reviews = []  # each reviewer launch writes the next of these reports, if any
        self.repairs = []  # each writer launch hands back with the next of these headers, if any
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"].update(launch=RESUMABLE, session="--session-id {id}", resume="--resume {id}")

        def keys(workspace, surface, items):
            for i, w in self.state()["workers"].items():
                if surface in (w.get("surface_id"), w["surface"]) and items == ["/quit", "enter"]:
                    self.receipt(i)  # quitting a worker writes its exit receipt
                    return

        real_launch = fleet.launch_one

        def launch_one(a, cfg):
            label, w = real_launch(a, cfg)
            if w.get("role") == "review" and self.reviews:
                self.write_report(w["id"], **self.reviews.pop(0))
            elif w.get("role") == "implementation" and self.repairs:
                self.hand_back(w["id"], **self.repairs.pop(0))
            return label, w

        for mocker in (patch.object(fleet, "send_keys", side_effect=keys),
                       patch.object(fleet, "launch_one", side_effect=launch_one)):
            mocker.start()
            self.addCleanup(mocker.stop)

    # ---------- helpers ----------

    def run_file(self, *parts, run="demo"):
        return self.repo.joinpath(".fleet/runs", run, *parts)

    def write_report(self, wid, prose="Done.\n", **fields):
        data = {"files_changed": [], "checks": [], "criteria": [], "findings": [], "questions": [], **fields}
        self.run_file("reports", f"{wid}.md").write_text(
            f"# Report {wid}\nStatus: needs-verification\n\n```fleet\n{json.dumps(data)}\n```\n\n{prose}")

    def hand_back(self, ticket, change="implemented change\n", **fields):
        """The writer edits its own file and hands back with a clean header; fields add to it."""
        t = self.state()["tickets"][ticket]
        own = t["files"][0]
        (self.repo / own).write_text(change)
        fields.setdefault("files_changed", [own])
        # An honest header reports every planned command with its exit code, as the strict bar requires.
        fields.setdefault("checks", [{"run": c["run"], "exit": 0} for c in t.get("commands", [])])
        self.write_report(ticket, **fields)

    def plan(self, tickets):
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "cross-heavy")
        for t in tickets:
            path = self.repo / f"{t['id']}.md"
            path.write_text(f"# Ticket {t['id']}\nImplement the owned change.\n")
            t.update(ticket=str(path), provider="writer", context="Agreed scope.")
            t.setdefault("tier", "standard")
            t.setdefault("checks", [{"run": "true"}])
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": tickets, "decisions": ["Bounded local checks."]}))
        self.call("plan", "demo", "--file", str(plan))
        for t in tickets:
            self.prompt(t["id"])

    def usage(self, *args, expected=0):
        return self.call("usage", *args, expected=expected)

    def usage_json(self, *args):
        return json.loads(self.usage(*args, "--json"))

    def tickets(self, u):
        return {t["ticket"]: t for t in u["tickets"]}

    def scripted(self):
        """Five tickets, one after another:
        01 heavy: reviewed with two findings, fixed through send --findings (a --resume repair), re-reviewed clean
           and accepted;
        02: its first hand-back fails the gate loop once (a gate round, no relaunch), then passes and is accepted;
        03: hits a usage limit before handing back, is resumed and accepted (the resume is no repair);
        04: accepted on its first hand-back;
        05: exits without handing back, is relaunched fresh (a repair), then dropped."""
        self.plan([{"id": "01", "files": ["owned.txt"], "tier": "heavy"},
                   {"id": "02", "files": ["b.txt"], "checks": [{"run": "grep -q fixed b.txt"}]},
                   {"id": "03", "files": ["c.txt"]}, {"id": "04", "files": ["d.txt"]}, {"id": "05", "files": ["e.txt"]}])
        ev = ["--evidence", str(self.evidence)]

        self.launch("01", tier="heavy")
        self.hand_back("01", checks=[{"run": "true", "exit": 0}])
        self.reviews += [{"findings": [HIGH, LOW], "prose": "## Findings\n- High: drops a line.\n- Low: wording.\n"}, {}]
        self.repairs.append({"change": "repaired change\n", "checks": [{"run": "true", "exit": 0}], "responses": FIXED})
        out = self.call("supervise", "demo", "--timeout", "20", "--interval", "1", "--settle", "0",
                        "--auto", "gate,review,repair")
        self.assertIn("READY 01", out)
        self.call("accept", "demo", "01", "--gate", *ev)

        self.launch("02")
        self.hand_back("02", change="draft\n", checks=[{"run": "grep -q fixed b.txt", "exit": 1}])
        self.assertNotIn("REPORT", self.call("wait", "demo", "--gate", "--timeout", "0", "--settle", "0", expected=2))
        self.hand_back("02", change="fixed\n")
        self.assertIn("REPORT 02", self.call("wait", "demo", "--gate", "--timeout", "0", "--settle", "0"))
        self.call("accept", "demo", "02", *ev)

        self.launch("03")
        self.receipt("03", returncode=1)  # a usage limit: no report
        self.call("status", "demo")
        self.call("launch", "demo", "03", "writer", "--tier", "standard", "--resume")
        self.hand_back("03")
        self.call("accept", "demo", "03", *ev)

        self.launch("04")
        self.hand_back("04")
        self.call("accept", "demo", "04", *ev)

        self.launch("05")
        self.receipt("05", returncode=1)
        self.call("status", "demo")
        self.launch("05")
        self.receipt("05", returncode=1)
        self.call("status", "demo")
        self.call("drop", "demo", "05", "--reason", "the owner no longer needs it")

    def legacy_run(self, run="old", schema=2):
        """A run as an earlier release left it: no ledger, no review_rounds, reports without a fleet header."""
        d = self.run_file(run=run)
        (d / "reports").mkdir(parents=True)
        now = time.time()
        state = {"schema": schema, "run": run, "repo": str(self.repo), "options": {}, "decisions": [], "prompts": {},
                 "tickets": {"01": {"id": "01", "status": "verified", "tier": "standard", "files": ["owned.txt"],
                                    "blockers": []},
                             "02": {"id": "02", "status": "pending", "tier": "light", "files": ["x.txt"],
                                    "blockers": []}},
                 "workers": {"01": {"id": "01", "role": "implementation", "provider": "writer", "model": "old-model",
                                    "tier": "standard", "state": "verified", "token": "t1", "launched_at": now - 600,
                                    "exit_file": str(d / "exits" / "01.t1.json")},
                             "review-01": {"id": "review-01", "role": "review", "review_of": "01",
                                           "provider": "reviewer", "model": "old-review", "tier": "review",
                                           "state": "exited", "token": "t2", "launched_at": now - 300,
                                           "exit_file": str(d / "exits" / "review-01.t2.json")},
                             "02": {"id": "02", "role": "implementation", "provider": "writer", "model": "old-model",
                                    "tier": "light", "state": "exited", "token": "t3", "launched_at": now - 200}}}
        (d / "run.json").write_text(json.dumps(state))
        (d / "reports" / "01.md").write_text("# Report\nStatus: verified\n\nDone.\n")
        (d / "reports" / "review-01.md").write_text("# Review\nStatus: needs-verification\n\nLooks fine.\n")
        return d

    def snapshot(self):
        root = self.repo / ".fleet"
        return {str(f): (f.read_bytes(), f.stat().st_mtime_ns) for f in sorted(root.rglob("*")) if f.is_file()}


class Usage(UsageCase):
    def test_rework_and_first_pass_match_a_scripted_history(self):
        self.scripted()
        u = self.usage_json("demo")
        t = self.tickets(u)
        self.assertEqual({i: x["outcome"] for i, x in t.items()},
                         {"01": "verified", "02": "verified", "03": "verified", "04": "verified", "05": "dropped"})
        self.assertEqual({i: x["repairs"] for i, x in t.items()}, {"01": 1, "02": 0, "03": 0, "04": 0, "05": 1})
        self.assertEqual({i: x["gate_rounds"] for i, x in t.items()}, {"01": 0, "02": 1, "03": 0, "04": 0, "05": 0})
        self.assertEqual({i: x["review_rounds"] for i, x in t.items()}, {"01": 2, "02": 0, "03": 0, "04": 0, "05": 0})
        self.assertEqual({i: x["first_pass"] for i, x in t.items()},
                         {"01": False, "02": False, "03": True, "04": True, "05": None})  # a drop is neither
        self.assertEqual((t["01"]["findings"], t["01"]["finding_rounds"]), (2, 2))      # 2 findings, then 0
        self.assertIsNone(t["02"]["findings"])                                           # never reviewed
        total = u["totals"]
        self.assertEqual({k: total[k] for k in ("tickets", "verified", "first_pass", "dropped", "open", "repairs",
                                                "gate_rounds", "review_rounds", "findings", "finding_rounds",
                                                "launches")},
                         {"tickets": 5, "verified": 4, "first_pass": 2, "dropped": 1, "open": 0, "repairs": 2,
                          "gate_rounds": 1, "review_rounds": 2, "findings": 2, "finding_rounds": 2, "launches": 10})
        self.assertEqual(u["dropped"], ["05"])
        self.assertEqual(u["groups"]["01"]["launches"], 4)  # two writer launches and two reviewers

        out = self.usage("demo")
        self.assertIn("usage of run demo, by ticket (a reviewer's launches count toward the ticket it reviews):", out)
        self.assertIn("total: 5 tickets (4 verified, 1 dropped); first pass 2 of 4 verified (50%); repairs 2, "
                      "gate rounds 1, review rounds 2; findings 1.0 per round (2 in 2 rounds)", out)
        self.assertIn("  05        1 ticket (1 dropped); first pass none verified; repairs 1, gate rounds 0", out)
        self.assertIn("10 launches, wall ", out)
        self.assertIn("tokens unknown", out)
        self.assertIn("dropped, neither a pass nor a fail: 05", out)

    def test_grouping_puts_ticket_numbers_on_the_writer_and_launches_on_their_own_group(self):
        self.scripted()
        for by in ("provider", "role"):
            with self.subTest(by=by):
                g = self.usage_json("demo", "--by", by)["groups"]
                writer = g["writer"]
                self.assertEqual((writer["tickets"], writer["first_pass"], writer["launches"]), (5, 2, 8))
                self.assertEqual(writer["findings"], 2)  # findings count toward the reviewed writer's group
                self.assertEqual((g["reviewer"]["tickets"], g["reviewer"]["launches"]), (0, 2))
        g = self.usage_json("demo", "--by", "model")["groups"]
        self.assertEqual(sorted(g), ["reviewer-model", "writer-model"])
        self.assertEqual(g["writer-model"]["tickets"], 5)
        g = self.usage_json("demo", "--by", "tier")["groups"]
        self.assertEqual({k: (v["tickets"], v["launches"]) for k, v in g.items()},
                         {"heavy": (1, 2), "review": (0, 2), "standard": (4, 6)})
        out = self.usage("demo", "--by", "provider")
        self.assertIn("ticket numbers go to the ticket's first writer, launches to their own provider", out)
        self.assertIn("  reviewer  no ticket of its own\n", out)
        self.assertIn("2 launches, wall ", out)
        self.assertIn(self.usage("--by", "provider"), out)  # no run named: the latest

    def test_unknown_tokens_are_never_zero(self):
        self.scripted()
        u = self.usage_json("demo")
        self.assertIsNone(u["totals"]["tokens"])
        self.assertEqual(u["totals"]["tokens_unknown"], 10)
        state = json.loads(self.run_file("run.json").read_text())
        state["ledger"][0]["tokens"] = {"input": 1200, "output": 300}  # one launch's CLI reported usage
        fleet.save_run(self.repo, "demo", state)
        u = self.usage_json("demo")
        self.assertEqual(u["totals"]["tokens"], {"input": 1200, "output": 300})
        self.assertEqual(u["totals"]["tokens_unknown"], 9)
        self.assertIsNone(u["groups"]["02"]["tokens"])
        out = self.usage("demo")
        self.assertIn("tokens in 1.2K out 300 (unknown for 9 of 10 workers)", out)
        self.assertNotIn("in 0", out)

    def test_all_reads_every_run_and_skips_one_it_cannot_read(self):
        self.scripted()
        self.legacy_run("old")
        self.legacy_run("ancient", schema=1)
        u = self.usage_json("--all")
        self.assertEqual(u["runs"], ["demo", "old"])
        self.assertEqual([s["run"] for s in u["skipped"]], ["ancient"])
        self.assertIn("demo/01", u["groups"])
        self.assertIn("old/01", u["groups"])
        self.assertEqual(u["totals"]["tickets"], 7)
        self.assertIn("skipped run ancient: legacy run", self.usage("--all"))
        self.assertEqual(self.usage_json("demo", "old")["runs"], ["demo", "old"])
        self.assertIn("legacy run", self.usage("ancient", expected=1))   # a named run must be readable
        self.assertIn("not both", self.usage("demo", "--all", expected=1))

    def test_a_run_from_before_the_ledger_and_the_header_reads_without_errors(self):
        self.legacy_run("old")
        u = self.usage_json("old")
        t = self.tickets(u)
        self.assertEqual(u["totals"]["launches"], 3)                      # the latest launch of each worker
        self.assertEqual((t["01"]["outcome"], t["01"]["first_pass"], t["01"]["review_rounds"]), ("verified", True, 1))
        self.assertIsNone(t["01"]["findings"])                           # no header: unknown, not zero
        self.assertEqual(t["02"]["outcome"], "open")
        self.assertIsNone(u["totals"]["tokens"])
        out = self.usage("old")
        self.assertIn("findings unknown (1 review rounds, no readable reviewer header)", out)
        self.assertIn("wall unknown, tokens unknown", out)
        self.assertNotIn("ledger", json.loads(self.run_file("run.json", run="old").read_text()))

    def test_a_report_parked_by_send_changes_counts_as_changes_requested(self):
        self.plan([{"id": "01", "files": ["owned.txt"]}])
        self.launch("01")
        self.hand_back("01")
        self.assertFalse(self.tickets(self.usage_json())["01"]["changes_requested"])
        self.call("send", "demo", "01", "--changes", "Keep the second line.")
        t = self.tickets(self.usage_json())["01"]
        self.assertEqual((t["changes_requested"], t["repairs"], t["outcome"], t["first_pass"]),
                         (True, 0, "open", None))

    def test_a_report_that_is_not_utf8_never_stops_the_report(self):
        """#53 review, 6: usage reads every report tolerantly, an archived one included."""
        self.plan([{"id": "01", "files": ["owned.txt"]}])
        self.launch("01")
        self.hand_back("01")
        self.run_file("reports", "01." + "ab" * 16 + ".previous").write_bytes(
            b"# Report \xff\xfe\nStatus: changes-requested\n\nDone.\n")
        t = self.tickets(self.usage_json("demo"))["01"]
        self.assertTrue(t["changes_requested"])
        self.assertIn("total: 1 ticket (1 open)", self.usage("demo"))

    def test_all_lists_a_run_folder_whose_name_is_no_run_id_as_skipped(self):
        """#53 review, 7: one folder Fleet cannot name never aborts the whole report."""
        self.plan([{"id": "01", "files": ["owned.txt"]}])
        self.launch("01")
        bad = self.repo / ".fleet" / "runs" / "bad.name"
        bad.mkdir()
        (bad / "run.json").write_text("{}")
        u = self.usage_json("--all")
        self.assertEqual(u["runs"], ["demo"])
        self.assertEqual([s["run"] for s in u["skipped"]], ["bad.name"])
        self.assertIn("skipped run bad.name: run/worker IDs must be", self.usage("--all"))
        self.assertIn("run/worker IDs must be", self.usage("bad.name", expected=1))  # a named run must be valid

    def test_it_writes_nothing_and_takes_no_lock(self):
        self.scripted()
        self.legacy_run("old")
        before = self.snapshot()
        with patch.object(fleet, "try_lock", side_effect=AssertionError("usage took the lock")):
            for args in (("demo",), ("old", "--json"), ("--all", "--by", "model"), ()):
                self.usage(*args)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
