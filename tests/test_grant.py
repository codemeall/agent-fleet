"""fleet grant: one more file for a pending ticket, with its launch content as the baseline."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


class Grant(RuntimeCase):
    def setUp(self):
        super().setUp()
        # Written before the quality bar: these tests cover verify and accept's mechanics with header-less
        # reports, so their runs start under acceptance = "warn". tests/test_quality_bar.py covers "strict".
        self.cfg["defaults"]["acceptance"] = "warn"
        self.typed = []  # every item list fleet.send_keys was given
        typer = patch.object(fleet, "send_keys", side_effect=lambda ws, surface, items: self.typed.append(items))
        typer.start()
        self.addCleanup(typer.stop)

    def grant(self, ticket, *files, reason="worker needs it", expected=0, extra=()):
        return self.call("grant", "demo", ticket, *files, "--reason", reason, *extra, expected=expected)

    def inbox(self, ticket="01"):
        return self.repo / ".fleet/runs/demo/inbox" / (ticket + ".md")

    def snapshot(self, ticket="01"):
        return json.loads((self.repo / ".fleet/runs/demo/snapshots" / (ticket + ".json")).read_text())

    def two_live_slots(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.cfg["providers"]["writer"]["max"] = 2

    # ---------- what a grant does ----------

    def test_a_grant_before_launch_widens_the_scope_and_launch_captures_it(self):
        self.setup_run()
        out = self.grant("01", "extra.txt", reason="the parser lives there")
        self.assertIn("01: granted extra.txt; its scope is now extra.txt, owned.txt", out)
        self.assertIn("fleet prompt demo 01 --force before its next launch", out)  # setup_run wrote its prompt
        t = self.state()["tickets"]["01"]
        self.assertEqual(t["files"], ["extra.txt", "owned.txt"])
        self.assertEqual(t["grants"][0]["files"], ["extra.txt"])
        self.assertEqual(t["grants"][0]["reason"], "the parser lives there")
        self.assertRegex(self.state()["decisions"][-1],
                         r"\AGranted extra.txt to 01: the parser lives there \(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\)\Z")
        self.assertFalse(self.inbox().exists())  # never launched: its prompt carries the scope
        self.assertIn("files=extra.txt, owned.txt", self.call("resume", "demo"))
        self.prompt("01", "--force")
        self.assertIn("extra.txt, owned.txt", (self.repo / ".fleet/runs/demo/prompts/01.md").read_text())
        self.launch()
        self.assertIn("extra.txt", self.snapshot())

    def test_a_stray_edit_made_before_the_grant_shows_in_the_diff_and_verify_then_passes(self):
        self.commit_other()
        self.setup_run()
        self.launch()
        (self.repo / "owned.txt").write_text("in scope\n")
        (self.repo / "other.txt").write_text("stray edit\n")       # clean at launch
        (self.repo / "helper.txt").write_text("new helper\n")      # absent at launch
        out = self.grant("01", "other.txt", "helper.txt", reason="the fix spans the helper")
        self.assertIn("sent message 1 to 01", out)
        pointer = f'Lead message 1: read "## Message 1" in {self.inbox()}, then continue.'
        self.assertEqual(self.typed, [[pointer, "enter"]])
        message = self.inbox().read_text()
        self.assertIn("granted you helper.txt, other.txt: the fix spans the helper", message)
        self.assertIn("once this worker exits", out)
        self.hand_back()
        diff = self.call("diff", "demo", "01")
        self.assertIn("-tracked elsewhere\n+stray edit\n", diff)  # the launch content, not the content at grant time
        self.assertIn("--- /dev/null\n+++ b/helper.txt\n", diff)
        self.assertIn("+in scope\n", diff)
        self.verify()  # the whole-tree check now counts both files as 01's
        self.assertEqual(self.state()["tickets"]["01"]["status"], "verified")

    def test_a_stopped_worker_finds_the_grant_in_its_inbox(self):
        self.setup_run()
        self.launch()
        self.stop_with_receipt()
        self.typed.clear()
        out = self.grant("01", "extra.txt", reason="config moves there")
        self.assertEqual(self.typed, [])
        self.assertIn("reads it when relaunched", out)
        self.assertIn("fleet prompt demo 01 --force before its next launch", out)
        self.assertIn("granted you extra.txt: config moves there", self.inbox().read_text())
        self.assertIsNone(self.snapshot()["extra.txt"])  # absent at launch

    def test_a_failed_pointer_keeps_the_grant_and_the_message(self):
        self.setup_run()
        self.launch()
        with patch.object(fleet, "send_keys", side_effect=fleet.FleetError("socket unavailable")):
            out = self.grant("01", "extra.txt", expected=1)
        self.assertIn("the grant is saved and message 1 is in", out)
        self.assertIn("do not grant or send again", out)
        self.assertIn("extra.txt", self.state()["tickets"]["01"]["files"])
        self.assertIn("granted you extra.txt", self.inbox().read_text())

    def test_a_file_already_in_scope_grants_nothing(self):
        self.setup_run()
        self.assertIn("already in its scope; nothing granted",
                      self.call("grant", "demo", "01", "--reason", "files may follow the option", "owned.txt"))
        self.assertNotIn("grants", self.state()["tickets"]["01"])

    def test_a_dirty_file_unchanged_since_launch_keeps_its_launch_content(self):
        self.commit_other()
        (self.repo / "other.txt").write_text("owner's edit before the run\n")
        self.setup_run()
        self.launch()
        self.grant("01", "other.txt")
        (self.repo / "other.txt").write_text("worker's edit after the grant\n")
        self.hand_back()
        self.assertIn("-owner's edit before the run\n+worker's edit after the grant\n", self.call("diff", "demo", "01"))

    def test_a_staged_file_changed_since_launch_is_rebuilt_from_its_blob(self):
        self.commit_other()
        (self.repo / "other.txt").write_text("owner staged this\n")
        self.git("add", "other.txt")
        self.setup_run()
        self.launch()
        (self.repo / "other.txt").write_text("stray edit\n")
        self.grant("01", "other.txt")
        self.hand_back()
        self.assertIn("-owner staged this\n+stray edit\n", self.call("diff", "demo", "01"))

    def test_a_dirty_file_changed_since_launch_needs_accept_current(self):
        self.commit_other()
        (self.repo / "other.txt").write_text("owner's edit before the run\n")
        self.setup_run()
        self.launch()
        (self.repo / "other.txt").write_text("changed again\n")
        before = self.snapshot()
        refused = self.grant("01", "other.txt", expected=1)
        self.assertIn("other.txt (already changed when it launched", refused)
        self.assertIn("--accept-current", refused)
        self.assertNotIn("other.txt", self.state()["tickets"]["01"]["files"])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.inbox().exists())
        self.grant("01", "other.txt", reason="owner confirmed the file", extra=("--accept-current",))
        self.assertIn("baseline is the current content of other.txt (--accept-current)", self.state()["decisions"][-1])
        self.assertEqual(self.state()["tickets"]["01"]["grants"][0]["accept_current"], ["other.txt"])
        self.hand_back()
        self.assertNotIn("other.txt", self.call("diff", "demo", "01"))

    def test_a_file_another_ticket_owned_meanwhile_keeps_that_tickets_edits(self):
        self.two_live_slots()
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "03", "files": ["third.txt"]}])
        self.launch("01")
        self.launch("03")
        (self.repo / "third.txt").write_text("03's change\n")
        self.hand_back("03")
        self.verify("03")
        out = self.grant("01", "third.txt")
        self.assertIn("03 owned it while 01 ran", out)
        (self.repo / "third.txt").write_text("03's change\n01's addition\n")
        self.hand_back("01")
        diff = self.call("diff", "demo", "01")
        self.assertNotIn("+03's change", diff)
        self.assertIn("+01's addition", diff)

    # ---------- review ----------

    def test_a_review_captured_before_the_grant_goes_stale(self):
        self.commit_other()
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("in scope\n")
        (self.repo / "other.txt").write_text("stray edit\n")
        self.hand_back()
        diff = self.repo / "review.diff"
        self.call("diff", "demo", "01", "--output", str(diff), "--unowned-ok", "owner says it is theirs")
        self.prompt("review-01", "--review-of", "01", "--diff", str(diff))
        self.launch("review-01", "reviewer", "review")
        self.report("review-01")
        self.stop_with_receipt("review-01")
        self.verify("review-01")
        self.grant("01", "other.txt", reason="it was the worker's after all")
        self.assertIn("required cross-family review is missing, unverified or stale", self.verify(expected=1))
        self.assertEqual(self.state()["tickets"]["01"]["status"], "pending")

    def test_no_grant_while_the_tickets_reviewer_runs(self):
        self.setup_run(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("in scope\n")
        self.hand_back()
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        refused = self.grant("01", "extra.txt", expected=1)
        self.assertIn("01's reviewer may still be running", refused)
        self.assertNotIn("extra.txt", self.state()["tickets"]["01"]["files"])

    # ---------- refusals ----------

    def test_refuses_a_file_a_live_worker_of_another_ticket_holds(self):
        self.setup_run()  # 02 waits on 01, so only the live worker stands in the way
        self.launch("01")
        refused = self.grant("02", "owned.txt", expected=1)
        self.assertIn("01's worker is live", refused)
        self.assertNotIn("owned.txt", self.state()["tickets"]["02"]["files"])

    def test_refuses_a_file_of_an_unrelated_unverified_ticket_but_not_of_a_dependent(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"], "blockers": ["01"]},
                                {"id": "03", "files": ["third.txt"]}])
        self.assertIn("overlap unverified ticket 03", self.grant("01", "third.txt", expected=1))
        self.grant("01", "next.txt")  # 02 waits on 01, so their diffs never overlap in time

    def test_refuses_investigate_verified_and_dropped_tickets(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                                {"id": "inv", "kind": "investigate", "files": []}])
        self.assertIn("investigate ticket", self.grant("inv", "notes.txt", expected=1))
        self.call("drop", "demo", "02", "--reason", "owner abandoned it")
        self.assertIn("02 is already dropped", self.grant("02", "extra.txt", expected=1))
        self.launch("01")
        (self.repo / "owned.txt").write_text("in scope\n")
        self.hand_back("01")
        self.verify("01")
        self.assertIn("01 is already verified", self.grant("01", "extra.txt", expected=1))
        self.assertIn("no ticket 'review-01'", self.grant("review-01", "extra.txt", expected=1))

    def test_refuses_protected_paths_and_an_empty_reason(self):
        self.setup_run()
        self.assertIn("protected file", self.grant("01", ".env", expected=1))
        self.assertIn("exact relative file", self.grant("01", "../outside.txt", expected=1))
        self.assertIn("--reason", self.grant("01", "extra.txt", reason="  ", expected=1))
        self.assertEqual(self.state()["tickets"]["01"]["files"], ["owned.txt"])


if __name__ == "__main__":
    import unittest
    unittest.main()
