"""fleet send's durable inbox: messages saved per ticket, a one-line pointer typed into the pane."""
import io
import shlex
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


class Inbox(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.typed = []  # every item list fleet.send_keys was given
        typer = patch.object(fleet, "send_keys", side_effect=lambda ws, surface, items: self.typed.append(items))
        typer.start()
        self.addCleanup(typer.stop)

    def inbox(self, ticket="01"):
        return self.repo / ".fleet/runs/demo/inbox" / (ticket + ".md")

    def report_path(self, ticket="01"):
        return self.repo / ".fleet/runs/demo/reports" / (ticket + ".md")

    def test_a_multi_line_message_is_saved_intact_and_the_pane_gets_one_line(self):
        self.setup_run()
        self.launch()
        message = "Changes requested:\n1) return null on 404\n2) add that case to the test"
        out = self.call("send", "demo", "01", message)
        self.assertIn("sent message 1 to 01", out)
        saved = self.inbox().read_text()
        self.assertRegex(saved, r"\A## Message 1 \(\d{4}-\d\d-\d\dT[\d:]+\)\n\n")
        self.assertIn(message + "\n", saved)
        pointer = f'Lead message 1: read "## Message 1" in {self.inbox()}, then continue.'
        self.assertEqual(self.typed, [[pointer, "enter"]])
        self.assertNotIn("\n", pointer)
        self.assertIn("last_send", self.state()["workers"]["01"])

    def test_message_numbers_continue_across_a_relaunch(self):
        self.setup_run()
        self.launch()
        self.call("send", "demo", "01", "first")
        self.stop_with_receipt()
        self.launch()  # a relaunch replaces the worker record
        self.call("send", "demo", "01", "second")
        saved = self.inbox().read_text()
        self.assertIn("## Message 1 (", saved)
        self.assertIn("## Message 2 (", saved)
        self.assertLess(saved.index("first"), saved.index("second"))
        self.assertIn('Lead message 2: read "## Message 2"', self.typed[-1][0])
        self.assertEqual(self.state()["inbox"]["01"]["count"], 2)

    def test_a_repeated_key_sends_nothing(self):
        self.setup_run()
        self.launch()
        self.call("send", "demo", "01", "Use the short copy.", "--key", "copy")
        out = self.call("send", "demo", "01", "Use the short copy.", "--key", "copy")
        self.assertIn("already sent as message 1; nothing sent", out)
        self.assertEqual(len(self.typed), 1)
        self.assertEqual(self.inbox().read_text().count("## Message"), 1)
        self.call("send", "demo", "01", "Another.", "--key", "other")  # a new key is a new message
        self.assertEqual(self.inbox().read_text().count("## Message"), 2)

    def test_changes_parks_the_report_before_the_message_goes_out(self):
        self.setup_run()
        self.launch()
        self.report()
        statuses = []
        with patch.object(fleet, "send_keys",
                          side_effect=lambda *_: statuses.append(fleet.parse_status(self.report_path().read_text()))):
            self.call("send", "demo", "01", "--changes", "Add the 404 test.", "--key", "fix-1")
        self.assertEqual(statuses, ["changes-requested"])
        text = self.report_path().read_text()
        self.assertIn("Status: changes-requested\n", text)
        self.assertIn("Checks passed; lead must inspect.", text)  # the rest of the report is untouched
        self.assertEqual(fleet.pending_reports(self.report_path().parent), [])  # wait ignores a parked report
        # A retry with the same key is not refused for the report it already parked.
        self.assertIn("nothing sent", self.call("send", "demo", "01", "--changes", "Add the 404 test.",
                                                "--key", "fix-1"))

    def test_a_blocked_report_the_lead_answered_is_no_longer_pending(self):
        """Final review, 10: wait's PENDING lines take a send's answer as handoff does (awaiting_lead)."""
        self.setup_run()
        self.launch()
        self.report_path().write_text("# Worker report\nStatus: blocked\n\nWhich copy?\n")
        self.assertIn("REPORT 01 [blocked]", self.call("wait", "demo", "--timeout", "0", "--settle", "0"))
        self.assertIn("PENDING 01 [blocked]", self.call("wait", "demo", "--timeout", "0", expected=2))
        self.call("send", "demo", "01", "Use the short copy.")
        out = self.call("wait", "demo", "--timeout", "0", expected=2)
        self.assertNotIn("PENDING 01", out)
        self.assertEqual(fleet.awaiting_lead(self.repo, self.state()), [])
        self.report_path().write_text("# Worker report\nStatus: blocked\n\nAnd the long copy?\n")  # asks again
        self.assertIn("REPORT 01 [blocked]", self.call("wait", "demo", "--timeout", "0", "--settle", "0"))
        self.assertIn("PENDING 01 [blocked]", self.call("wait", "demo", "--timeout", "0", expected=2))

    def test_changes_refuses_a_report_that_is_not_handed_back(self):
        self.setup_run()
        self.launch()
        out = self.call("send", "demo", "01", "--changes", "Fix it.", expected=1)
        self.assertIn("not missing", out)
        self.report_path().write_text("# Worker report\nStatus: blocked\n\nWhich copy?\n")
        out = self.call("send", "demo", "01", "--changes", "Fix it.", expected=1)
        self.assertIn("at needs-verification, not blocked", out)
        self.assertFalse(self.inbox().exists())
        self.assertEqual(self.typed, [])
        self.assertIn("Status: blocked", self.report_path().read_text())

    def test_file_reads_a_path_or_stdin_and_exactly_one_source_is_required(self):
        self.setup_run()
        self.launch()
        with patch.object(sys, "stdin", io.StringIO("1) one\n2) two\n")):
            self.call("send", "demo", "01", "--file", "-")
        self.assertIn("1) one\n2) two\n", self.inbox().read_text())
        fixes = self.repo / "fixes.md"
        fixes.write_text("3) three\n")
        self.call("send", "demo", "01", "--file", str(fixes))
        self.assertIn("3) three\n", self.inbox().read_text())
        self.assertIn("fixes.md", self.state()["lead_files"])  # never blamed on a worker by the scope check
        self.assertIn("not both", self.call("send", "demo", "01", "text", "--file", str(fixes), expected=1))
        self.assertIn("send needs a message", self.call("send", "demo", "01", expected=1))
        self.assertEqual(self.inbox().read_text().count("## Message"), 2)

    def test_a_failed_pointer_keeps_the_message_on_record(self):
        self.setup_run()
        self.launch()
        with patch.object(fleet, "send_keys", side_effect=fleet.FleetError("socket unavailable")):
            out = self.call("send", "demo", "01", "Use the short copy.", expected=1)
        self.assertIn(f"message 1 is saved in {self.inbox()}", out)
        self.assertIn("reads it when it next checks its inbox", out)
        self.assertIn("Use the short copy.", self.inbox().read_text())
        self.assertNotIn("last_send", self.state()["workers"]["01"])  # wait owes it no response yet
        self.assertEqual(self.state()["inbox"]["01"]["count"], 1)

    def test_a_ticket_with_no_running_worker_gets_the_message_on_record_only(self):
        self.setup_run()
        out = self.call("send", "demo", "02", "Start from the helper in owned.txt.")
        self.assertIn("no worker is running", out)
        self.assertIn("Start from the helper", self.inbox("02").read_text())
        self.assertEqual(self.typed, [])
        self.assertIn("no ticket or worker", self.call("send", "demo", "nope", "hi", expected=1))

    def test_every_prompt_kind_and_the_resume_line_name_the_inbox(self):
        self.cfg["providers"]["writer"].update(launch="{bin} {session} --model {model} --effort {effort} {prompt}",
                                               session="--session-id {id}", resume="--resume {id}")
        self.setup_run(review="cross-all", tickets=[{"id": "01", "files": ["owned.txt"]},
                                                    {"id": "why", "files": [], "kind": "investigate"}])
        prompts = self.repo / ".fleet/runs/demo/prompts"
        self.assertIn(str(self.inbox("01")), (prompts / "01.md").read_text())
        self.assertIn(str(self.inbox("why")), (prompts / "why.md").read_text())
        self.launch()
        self.receipt(returncode=1)
        self.call("status", "demo")
        self.call("launch", "demo", "01", "writer", "--resume")
        line = shlex.split(self.state()["workers"]["01"]["command"])[-1]
        self.assertIn(f"read {self.inbox('01')} if it exists", line)
        self.hand_back()
        self.review_prompt()
        self.assertIn(str(self.inbox("review-01")), (prompts / "review-01.md").read_text())

    def test_an_identical_hand_back_in_the_same_mtime_tick_after_changes_still_wakes(self):
        self.setup_run()
        self.launch()
        self.report()
        report = self.repo / ".fleet/runs/demo/reports/01.md"
        first, stamp = report.read_text(), report.stat().st_mtime_ns
        self.assertIn("REPORT 01 [needs-verification]", self.call("wait", "demo", "--timeout", "0", "--settle", "0"))
        self.call("send", "demo", "01", "--changes", "fix the edge case")
        report.write_text(first)                                           # the same bytes, the same tick
        os.utime(report, ns=(stamp, stamp))
        self.assertIn("REPORT 01 [needs-verification]", self.call("wait", "demo", "--timeout", "0", "--settle", "0"))

