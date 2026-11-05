"""TW22 (#53): the quality bar. Under [defaults] acceptance = "strict", the default for new runs, fleet verify and
fleet accept refuse a ticket below the bar, naming each item and its way out, before anything is stopped, merged or
verified where that can be seen in advance; --accept-anyway takes the owner's words and records each item they
overrode. accept_checks run the combined-tree checks in the checkout at accept, after a worktree ticket's merge, and
a passing run is reused while nothing changed. supervise's READY line says strict ok or names what would block. A
run started under "warn", and a run from before the bar, behave as before."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fanout import FanoutCase  # noqa: E402
from test_runtime import RuntimeCase, fleet  # noqa: E402
from test_worktrees import WorktreeCase  # noqa: E402

RUN = ".fleet/runs/demo"
HIGH = {"severity": "high", "file": "owned.txt:1", "confidence": "confirmed"}
MEDIUM = {"severity": "medium", "file": "owned.txt:2", "confidence": "suspected"}
LOW = {"severity": "low", "file": "owned.txt:3", "confidence": "suspected"}
OWNER = "Owner, 2026-11-05: ship it; the lint rule is being retired this week"
BAR = "below the strict acceptance bar (acceptance strict)"


def clean(**fields) -> dict:
    """A writer's header that meets the bar for the plan below: its file, its planned command at exit 0, and both of
    the source ticket's criteria met."""
    data = {"files_changed": ["owned.txt"], "checks": [{"run": "true", "exit": 0}],
            "criteria": [{"id": 1, "met": True, "where": "owned.txt"}, {"id": 2, "met": True, "where": "true"}],
            "findings": [], "questions": []}
    data.update(fields)
    return data


class QualityCase(RuntimeCase):
    """A run whose reviewers write the next queued report as they launch, and whose relaunched writer hands back
    with the next queued header, as the findings loop tests drive them."""

    def setUp(self):
        super().setUp()
        self.cfg["defaults"]["max_parallel"] = 2
        self.reviews, self.repairs = [], []

        def keys(workspace, surface, items):
            for i, w in self.state()["workers"].items():
                if surface in (w.get("surface_id"), w["surface"]) and items == ["/quit", "enter"]:
                    self.receipt(i)  # quitting a worker writes its exit receipt
                    return

        real_launch = fleet.launch_one

        def launch_one(a, cfg):
            label, w = real_launch(a, cfg)
            if w.get("role") == "review" and self.reviews:
                self.write(w["id"], self.reviews.pop(0))
            elif w.get("role") == "implementation" and self.repairs:
                self.hand_back(**self.repairs.pop(0))
            return label, w

        for mocker in (patch.object(fleet, "send_keys", side_effect=keys),
                       patch.object(fleet, "launch_one", side_effect=launch_one)):
            mocker.start()
            self.addCleanup(mocker.stop)

    # ---------- helpers ----------

    def plan(self, review="off", tier="standard", tickets=None, **defaults):
        self.cfg["defaults"].update(defaults)
        self.call("init", "demo", "--workspace", "workspace:1", "--review", review, *self.off_reason(review))
        tickets = tickets or [{"id": "01", "files": ["owned.txt"]}]
        for t in tickets:
            path = self.repo / f"{t['id']}.md"
            path.write_text(f"# Ticket {t['id']}\n- [ ] the change is made\n- [ ] the check passes\n")
            t.setdefault("tier", tier)
            t.setdefault("context", "Agreed scope.")
            t.setdefault("checks", [{"run": "true"}])
            t.update(ticket=str(path), provider="writer")
        plan = self.repo / "plan.json"
        plan.write_text(json.dumps({"tickets": tickets, "decisions": ["Bounded local checks."]}))
        self.call("plan", "demo", "--file", str(plan))
        for t in tickets:
            self.prompt(t["id"])

    def run_file(self, *parts):
        return self.repo.joinpath(RUN, *parts)

    def write(self, wid, data, prose="Done.\n"):
        """A report: `data` is a header's fields, None for no header, or a string for a raw block."""
        block = ("```fleet\n" + json.dumps(data) + "\n```\n\n" if isinstance(data, dict) else data or "")
        self.run_file("reports", f"{wid}.md").write_text(f"# Report {wid}\nStatus: needs-verification\n\n{block}{prose}")

    def hand_back(self, ticket="01", file="owned.txt", change="implemented change\n", header=None, prose="Done.\n",
                  **fields):
        (self.repo / file).write_text(change)
        self.write(ticket, header if header is not None else clean(**{"files_changed": [file], **fields}), prose)

    def accept(self, *args, ticket="01", expected=0):
        return self.call("accept", "demo", ticket, "--evidence", str(self.evidence), *args, expected=expected)

    def verify(self, *args, ticket="01", expected=0):
        return self.call("verify", "demo", ticket, "--evidence", str(self.evidence), *args, expected=expected)

    def status(self, wid="01"):
        return fleet.parse_status(self.run_file("reports", f"{wid}.md").read_text())

    def supervise(self, *args, expected=0, timeout="10"):
        return self.call("supervise", "demo", "--timeout", timeout, "--interval", "1", "--settle", "0", *args,
                         expected=expected)


# Each item the bar names for a writer's report, as (what the header holds, what the refusal names).
WRITER_ITEMS = (
    ("no header", None, "01's report has no fleet header"),
    ("unreadable", "```fleet\n{not json\n```\n\n", "01's fleet header cannot be read (invalid JSON)"),
    ("file out of scope", clean(files_changed=["owned.txt", "src/x.py"]), "01's header: src/x.py outside scope"),
    ("planned command not reported", clean(checks=[]), "01's header: true not reported"),
    ("planned command without an exit", clean(checks=[{"run": "true", "exit": None}]),
     "01's header: true has no exit code"),
    ("malformed field", clean(criteria="all of them"), "01's header: criteria must be a list"),
    ("fewer criteria than checkboxes", clean(criteria=[{"id": 1, "met": True}]),
     "01's header: 1 criteria for the ticket's 2 checkboxes"),
    ("unmet criterion", clean(criteria=[{"id": 1, "met": True}, {"id": 2, "met": False, "where": "not yet"}]),
     '01: criterion "2" is unmet'),
    ("non-zero exit", clean(checks=[{"run": "true", "exit": 0}, {"run": "make lint", "exit": 2}]),
     '01: check "make lint" reports exit 2'),
    ("open question", clean(questions=["Which copy wins?", ""]), '01: open question "Which copy wins?"'),
)


class Policy(QualityCase):
    def test_new_runs_start_strict_and_a_run_from_before_the_bar_reads_as_warn(self):
        self.plan()
        self.assertEqual(self.state()["options"]["acceptance"], "strict")
        self.assertEqual(self.state()["options"]["accept_checks"], [])
        self.assertEqual(fleet.acceptance_mode({"options": {}}), "warn")
        self.assertEqual(fleet.acceptance_mode({"options": {"acceptance": "warn"}}), "warn")

    def test_accept_refuses_each_writer_item_by_name_before_anything_is_stopped(self):
        self.plan()
        self.launch()
        for name, data, item in WRITER_ITEMS:
            with self.subTest(name):
                self.hand_back(header=data if data is not None else "")
                out = self.accept(expected=1)
                self.assertIn("01 not accepted; nothing was stopped or verified:", out)
                self.assertIn(f"- {BAR}:\n  - {item}. Way out: ", out)
                self.assertIn("--accept-anyway \"<owner's words>\", never without their words", out)
                state = self.state()
                self.assertEqual(state["workers"]["01"]["state"], "running")  # nothing was stopped
                self.assertEqual(state["tickets"]["01"]["status"], "pending")
                self.assertEqual(self.status(), "needs-verification")
        self.hand_back()  # the same ticket with a clean header goes through
        out = self.accept()
        self.assertIn("01: verified", out)
        self.assertNotIn("WARNING", out)

    def test_verify_refuses_each_writer_item_by_name_and_changes_nothing(self):
        self.plan()
        self.launch()
        self.hand_back()
        self.stop_with_receipt()
        decisions = self.state()["decisions"]
        for name, data, item in WRITER_ITEMS:
            with self.subTest(name):
                self.write("01", data)
                out = self.verify(expected=1)
                self.assertIn(f"01 is {BAR}, so it is not verified; nothing was changed:\n- {item}. Way out: ", out)
                state = self.state()
                self.assertEqual(state["tickets"]["01"]["status"], "pending")
                self.assertNotIn("verified", state["workers"]["01"])
                self.assertEqual(state["decisions"], decisions)
                self.assertEqual(self.status(), "needs-verification")

    def test_each_way_out_names_the_command(self):
        self.plan()
        self.launch()
        self.hand_back(questions=["Which copy wins?"], files_changed=["owned.txt", "src/x.py"])
        out = self.accept(expected=1)
        self.assertIn('01: open question "Which copy wins?". Way out: answer it with fleet send demo 01 "<answer>", '
                      "and have it hand back without the question", out)
        self.assertIn("01's header: src/x.py outside scope. Way out: fleet grant demo 01 <file> --reason \"<why>\" if "
                      "it is the ticket's own work; else send it back with fleet send demo 01 --changes \"<what to "
                      "fix>\"", out)

    def test_a_report_that_claims_success_while_its_header_shows_a_failed_check_is_refused(self):
        """The test history the ticket asks for: the worker's prose says every check passed; its own header says
        npm test exited 1. Fleet believes the header, refuses, and accepts once a real fix comes back."""
        self.plan(tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": "true"}, {"run": "npm test"}]}])
        self.launch()
        self.hand_back(checks=[{"run": "true", "exit": 0}, {"run": "npm test", "exit": 1}],
                       prose="All checks pass and every criterion is met. Ready to merge; the lead can accept now.\n")
        line = next(x for x in self.call("wait", "demo", "--timeout", "0", "--settle", "0").splitlines()
                    if x.startswith("REPORT 01 "))
        self.assertIn("checks 1/2 exit 0", line)
        out = self.accept(expected=1)
        self.assertIn('01: check "npm test" reports exit 1. Way out: send it back with fleet send demo 01 --changes',
                      out)
        self.assertEqual(self.state()["tickets"]["01"]["status"], "pending")
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        self.call("send", "demo", "01", "--changes", "npm test fails: fix it and report its real exit code")
        self.hand_back(change="fixed change\n", checks=[{"run": "true", "exit": 0}, {"run": "npm test", "exit": 0}])
        self.assertIn("01: verified", self.accept())
        self.assertIn("Status: verified", self.run_file("reports", "01.md").read_text())


    def test_one_accept_computes_the_bar_once_per_state_and_verify_reuses_it(self):
        """#53 review, 14: before the stop, and again on the state the stop and the gate left; verify reuses that."""
        self.plan()
        self.launch()
        self.hand_back()
        with patch.object(fleet, "quality_bar", wraps=fleet.quality_bar) as bar:
            self.assertIn("01: verified", self.accept("--gate"))
        self.assertEqual(bar.call_count, 2)


class Override(QualityCase):
    def test_accept_anyway_records_the_owners_words_and_each_item_it_overrode(self):
        self.plan()
        self.launch()
        self.hand_back(checks=[{"run": "true", "exit": 0}, {"run": "make lint", "exit": 2}],
                       questions=["Should the lint rule stay?"])
        out = self.accept("--accept-anyway", OWNER)
        self.assertIn("01: accepted regardless of the quality bar, by the owner's words; 2 item(s) overridden", out)
        self.assertIn("01: verified", out)
        decision = self.state()["decisions"][-1]
        self.assertTrue(decision.startswith(f"Accepted 01 regardless of the quality bar, by the owner: {OWNER}. It "
                                            'overrode: 01: check "make lint" reports exit 2; 01: open question '
                                            '"Should the lint rule stay?" ('), decision)
        report = self.run_file("reports", "01.md").read_text()
        self.assertIn(f"## Accepted anyway\n\nThe owner's words: {OWNER}\n\nThey overrode:\n"
                      '- 01: check "make lint" reports exit 2\n- 01: open question "Should the lint rule stay?"', report)
        self.assertIn("Status: verified", report)
        self.assertIn(OWNER, self.state()["workers"]["01"]["verification_evidence"])
        # Below the bar and kept by the owner's words: fleet usage never counts it as a first pass.
        usage = json.loads(self.call("usage", "demo", "--json"))
        t = next(x for x in usage["tickets"] if x["ticket"] == "01")
        self.assertEqual((t["outcome"], t["first_pass"], t["accepted_anyway"]), ("verified", False, True))
        self.assertIn("first pass 0 of 1 verified (0%), 1 accepted anyway", self.call("usage", "demo"))

    def test_verify_takes_the_override_too(self):
        self.plan()
        self.launch()
        self.hand_back(header="")
        self.stop_with_receipt()
        self.assertIn("01: verified", self.verify("--accept-anyway", OWNER))
        self.assertIn("It overrode: 01's report has no fleet header", self.state()["decisions"][-1])

    def test_the_override_needs_words_and_something_to_override_and_never_reaches_a_hard_item(self):
        self.plan(review="cross-all")
        self.launch()
        self.hand_back()
        out = self.accept("--accept-anyway", "  ", expected=1)
        self.assertIn("--accept-anyway needs the owner's words: ask the owner, and never pass it without them", out)
        out = self.accept("--accept-anyway", OWNER, expected=1)  # a required review is the run's review mode
        self.assertIn("01 needs a cross-family review first: fleet review demo 01", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")
        self.reviews.append(clean(files_changed=[], checks=[]))
        self.call("review", "demo", "01", "--settle", "0")
        out = self.accept("--accept-anyway", OWNER, expected=1)
        self.assertIn("--accept-anyway: 01 is not below the bar, so there is nothing to override; drop it", out)
        self.assertEqual(self.state()["tickets"]["01"]["status"], "pending")
        self.assertIn("01: verified", self.accept())

    def test_a_reviewer_is_verified_without_the_flags(self):
        self.plan(review="cross-all")
        self.launch()
        self.hand_back()
        self.reviews.append(clean(files_changed=[], checks=[]))
        self.call("review", "demo", "01", "--settle", "0")
        self.stop_with_receipt("review-01")
        out = self.verify("--accept-anyway", OWNER, ticket="review-01", expected=1)
        self.assertIn("review-01 is a reviewer; --accept-anyway and --accept-finding go with the ticket it reviews: "
                      "fleet verify demo 01 (or fleet accept)", out)


class Review(QualityCase):
    def reviewed(self, findings, tier="standard"):
        self.plan(review="cross-all", tier=tier)
        self.launch(tier=tier)
        self.hand_back()
        self.reviews.append(clean(files_changed=[], checks=[], findings=list(findings)))
        self.call("review", "demo", "01", "--settle", "0")

    def test_an_open_high_or_medium_finding_refuses_and_a_low_one_does_not(self):
        self.reviewed([HIGH, MEDIUM, LOW])
        out = self.accept(expected=1)
        self.assertIn('  - review-01: finding F1 (high, "owned.txt:1", confirmed) is open. Way out: fleet send demo 01 '
                      "--findings returns it to the writer; it is settled once a re-review no longer lists it\n", out)
        self.assertIn('  - review-01: finding F2 (medium, "owned.txt:2", suspected) is open.', out)
        self.assertNotIn("F3", out)
        self.assertNotIn("verified", self.state()["workers"]["review-01"])  # nothing was verified
        self.assertEqual(self.state()["workers"]["review-01"]["state"], "running")  # nor stopped

    def test_a_review_with_only_low_findings_meets_the_bar(self):
        self.reviewed([LOW])
        out = self.accept()
        self.assertIn("review-01: verified", out)
        self.assertIn("01: verified", out)

    def test_a_finding_is_settled_only_when_a_re_review_no_longer_lists_it(self):
        self.reviewed([HIGH, MEDIUM])
        self.call("send", "demo", "01", "--findings")
        fixed = {"finding": "F1", "action": "fixed", "evidence": "owned.txt keeps the line"}
        rebutted = {"finding": "F2", "action": "rebutted", "evidence": "line 2 is the owner's; the diff leaves it"}
        self.hand_back(change="repaired change\n", responses=[fixed, rebutted])
        # The re-review keeps F2 open: the writer's rebuttal is a claim, so the bar still refuses it.
        self.reviews.append(clean(files_changed=[], checks=[], findings=[{**MEDIUM, "id": "F2"}]))
        self.call("review", "demo", "01", "--settle", "0")
        out = self.accept(expected=1)
        self.assertIn('review-01: finding F2 (medium, "owned.txt:2", suspected) is open. Way out: fleet send demo 01 '
                      "--findings returns it to the writer; it is settled once a re-review no longer lists it, or "
                      "accept the writer's rebuttal with --accept-finding F2 \"<why>\"", out)
        self.assertNotIn("finding F1", out)                                # the re-review no longer lists it
        for args, problem in ((("F1", "it is fixed"), "--accept-finding F1: review-01 lists no open finding F1 (open: "
                                                      "F2)"),
                              (("F2", " "), "--accept-finding F2 needs your reason for accepting the writer's rebuttal"),
                              (("F7", "x"), "--accept-finding F7: review-01 lists no open finding F7")):
            with self.subTest(args=args):
                self.assertIn(problem, self.accept("--accept-finding", *args, expected=1))
        self.assertEqual(self.state()["tickets"]["01"]["status"], "pending")
        out = self.accept("--accept-finding", "F2", "line 2 predates the ticket; git blame shows the owner's commit")
        self.assertIn("01: verified", out)
        self.assertIn("Accepted 01's rebuttal of F2 on 01: line 2 predates the ticket; git blame shows the owner's "
                      "commit", self.state()["decisions"][-1])
        self.assertIn("## Accepted findings", self.run_file("reports", "01.md").read_text())

    def test_accept_finding_needs_a_rebuttal_and_a_strict_run(self):
        self.reviewed([HIGH])
        out = self.accept("--accept-finding", "F1", "looks fine to me", expected=1)
        self.assertIn("--accept-finding F1: the writer did not rebut F1, so there is no rebuttal to accept; fleet send "
                      "demo 01 --findings returns it to the writer", out)

    def test_a_review_whose_findings_cannot_be_counted_refuses(self):
        self.reviewed([])
        self.write("review-01", None)
        out = self.accept(expected=1)
        self.assertIn("review-01's fleet header cannot be read (it has none), so its findings cannot be counted. Way "
                      "out: fleet review demo 01 again", out)

    def test_the_latest_review_must_match_the_diff_even_when_an_older_verified_one_does(self):
        self.reviewed([])
        self.stop_with_receipt("review-01")
        self.verify(ticket="review-01")
        self.reviews.append(clean(files_changed=[], checks=[]))
        (self.repo / "owned.txt").write_text("edited after the review\n")
        self.call("review", "demo", "01", "--as", "review-01b", "--settle", "0")  # reviews this diff
        self.stop_with_receipt("review-01b")
        (self.repo / "owned.txt").write_text("implemented change\n")  # back to what review-01 saw
        items = fleet.quality_bar(self.repo, self.state(), "01")
        self.assertEqual([x[0] for x in items], ["review-01b reviewed an earlier diff of 01"])
        self.assertTrue(items[0][2])  # hard: the owner's words never override it
        out = self.verify("--accept-anyway", OWNER, expected=1)
        self.assertIn("- review-01b reviewed an earlier diff of 01. Way out: run fleet review demo 01 again (the "
                      "owner's words cannot override this)", out)


class Warn(QualityCase):
    def test_a_warn_run_verifies_and_accepts_as_before(self):
        self.plan(acceptance="warn")
        self.assertEqual(self.state()["options"]["acceptance"], "warn")
        self.launch()
        self.hand_back(header="")
        out = self.accept()
        self.assertIn("WARNING 01 header missing (not a refusal; check the report's prose)", out)
        self.assertIn("01: verified", out)
        self.assertNotIn("strict", out)

    def test_the_flags_are_refused_in_a_warn_run(self):
        self.plan(acceptance="warn")
        self.launch()
        self.hand_back(header="")
        out = self.accept("--accept-anyway", OWNER, "--accept-finding", "F1", "why", expected=1)
        self.assertIn("--accept-anyway: 01 is not below the bar (run demo's acceptance is warn), so there is nothing "
                      "to override; drop it", out)
        self.assertIn("--accept-finding: run demo's acceptance is warn, so no finding refuses 01; drop it", out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "running")

    def test_a_run_from_before_the_bar_accepts_as_before_with_no_reminder(self):
        self.plan()
        state = self.state()
        del state["options"]["acceptance"], state["options"]["accept_checks"]  # as an earlier Fleet saved it
        (self.repo / RUN / "run.json").write_text(json.dumps(state))
        self.launch()
        self.hand_back(header="")
        out = self.accept()
        self.assertIn("WARNING 01 header missing", out)
        self.assertIn("01: verified", out)
        self.assertNotIn("accept_checks", out)
        self.assertNotIn("ACCEPT-CHECKS", out)


class Investigate(QualityCase):
    def test_an_investigation_needs_a_readable_header_with_every_criterion_answered(self):
        self.plan(tickets=[{"id": "q", "kind": "investigate", "checks": [{"manual": "read-only research"}]}])
        self.launch("q")
        answered = {"files_changed": [], "checks": [], "findings": [HIGH], "questions": [],
                    "criteria": [{"id": 1, "met": True}, {"id": 2, "met": False, "where": "it does not hold"}]}
        for data, item in ((None, "q's report has no fleet header"),
                           ({**answered, "criteria": [{"id": 1, "met": True}]},
                            "q's header: 1 criteria for the ticket's 2 checkboxes"),
                           ({**answered, "files_changed": ["owned.txt"]},
                            "q's header: files_changed lists 1; an investigation changes none")):
            with self.subTest(item):
                self.write("q", data)
                out = self.accept(ticket="q", expected=1)
                self.assertIn(f"  - {item}. Way out: ", out)
                self.assertEqual(self.state()["workers"]["q"]["state"], "running")
        self.write("q", answered)  # an unmet criterion is an answer; a finding is what it found
        out = self.accept(ticket="q")
        self.assertIn("q: verified", out)
        self.assertNotIn("accept_checks", out)  # an investigation changes no files


class Fanout(FanoutCase):
    def test_each_investigator_and_the_judge_need_a_readable_header(self):
        self.launched()
        self.all_back()
        self.write_report("q--third", header=False, prose="Found it: the cache key.\n")
        out = self.call("accept", "demo", "q", "--evidence", str(self.evidence), expected=1)
        self.assertIn("q not accepted; nothing was stopped or verified:", out)
        self.assertIn(f"  - q--third's report has no fleet header. Way out: send it back with fleet send demo q--third "
                      "--changes", out)
        self.assertNotIn("q--writer", out.split(BAR)[1])
        self.assertTrue(all(w["state"] == "running" for w in self.state()["workers"].values()))
        self.call("stop", "demo", "q")
        self.assertIn("q--third's report has no fleet header",
                      self.call("verify", "demo", "q", "--evidence", str(self.evidence), expected=1))
        self.write_report("q--third", findings=[self.finding("high", "src/cache.py")], criteria=[{"id": 1, "met": True}])
        self.assertIn("q: verified with q--writer, q--reviewer, q--third",
                      self.call("accept", "demo", "q", "--evidence", str(self.evidence)))

    def test_a_judges_unreadable_header_refuses_and_the_owner_may_override_it(self):
        self.judged(header="```fleet\n{\n```\n\n")
        out = self.call("accept", "demo", "q", "--evidence", str(self.evidence), expected=1)
        self.assertIn("judge-q's fleet header cannot be read (invalid JSON)", out)
        out = self.call("accept", "demo", "q", "--evidence", str(self.evidence), "--accept-anyway", OWNER)
        self.assertIn("q: verified with q--writer, q--reviewer, q--third, judge-q", out)
        self.assertIn("It overrode: judge-q's fleet header cannot be read (invalid JSON)",
                      self.state()["decisions"][-1])
        self.assertIn("## Accepted anyway", self.run_file("reports", "judge-q.md").read_text())


class BestOfTwo(QualityCase):
    def test_a_picked_writer_meets_the_same_bar(self):
        """fleet accept --pick (#50) names the writer whose diff is accepted; the bar reads that writer's report."""
        self.plan()
        self.launch()
        self.hand_back()
        self.write("01--b", clean(checks=[{"run": "true", "exit": 0}, {"run": "npm test", "exit": 1}]))
        state = self.state()
        self.assertEqual(fleet.quality_bar(self.repo, state, "01"), [])
        items = fleet.quality_bar(self.repo, state, "01", writer="01--b")
        self.assertIn('01--b: check "npm test" reports exit 1', [x[0] for x in items])


class Ready(QualityCase):
    def test_ready_says_strict_ok_for_a_clean_ticket(self):
        self.plan(review="cross-heavy", tier="heavy")
        self.launch(tier="heavy")
        self.hand_back()
        self.reviews.append(clean(files_changed=[], checks=[]))
        out = self.supervise("--auto", "gate,review")
        self.assertIn("READY 01: gate passed (1/1), review clean (anthropic, findings 0), strict ok; fleet accept demo "
                      "01 --gate --evidence <file>", out)
        out = self.call("accept", "demo", "01", "--gate", "--evidence", str(self.evidence))
        self.assertIn("01: verified", out)

    def test_ready_names_what_would_block_before_the_lead_calls_accept(self):
        self.plan(review="cross-heavy", tier="heavy")
        self.launch(tier="heavy")
        self.hand_back(criteria=[{"id": 1, "met": True}, {"id": 2, "met": False}])
        self.reviews.append(clean(files_changed=[], checks=[]))
        self.call("review", "demo", "01", "--settle", "0")  # the lead reviewed it without supervise's own review
        out = self.supervise("--auto", "gate,review")
        line = next(x for x in out.splitlines() if x.startswith("READY 01: "))
        self.assertIn(', strict would block: 01: criterion "2" is unmet (fleet accept names the way out for each); '
                      "fleet accept demo 01", line)
        self.assertIn('01: criterion "2" is unmet', self.accept(expected=1))

    def test_ready_for_a_ticket_without_review_and_nothing_under_warn(self):
        self.plan(tier="standard", review="cross-heavy")
        self.launch()
        self.hand_back()
        out = self.supervise("--auto", "gate,review")
        self.assertIn("READY 01: gate passed (1/1), no review required (review cross-heavy, standard), strict ok; "
                      "fleet accept demo 01 --gate --evidence <file>", out)
        self.assertEqual(fleet.quality_note(self.repo, {**self.state(), "options": {}}, "01"), "")


class AcceptChecks(QualityCase):
    def test_with_none_set_accept_reminds_the_lead_that_the_combined_checks_are_its_own(self):
        self.plan()
        self.launch()
        self.hand_back()
        self.assertIn("NOTE 01: no accept_checks are set for this run, so the combined-tree checks (full suite, "
                      "typecheck, build) are yours to run in the checkout\n", self.accept())

    def test_a_failure_refuses_with_its_tail_before_anything_is_verified_and_the_owner_may_override_it(self):
        self.plan(accept_checks=[{"run": "echo combined tail line; exit 3", "timeout": 30}])
        self.assertEqual(self.state()["options"]["accept_checks"],
                         [{"run": "echo combined tail line; exit 3", "cwd": ".", "timeout": 30}])
        self.launch()
        self.hand_back()
        out = self.accept(expected=1)
        md = self.run_file("evidence", "01.accept-checks.md")
        self.assertIn(f"ACCEPT-CHECKS 01 0/1 passed in the checkout (echo combined tail line; exit 3: exit 3); "
                      f"evidence {md}", out)
        self.assertIn("01 not accepted: the combined checks (accept_checks) failed in the checkout: echo combined tail "
                      f"line; exit 3: exit 3. The failing tail is in {md}. Nothing was verified. Fix the combined "
                      "tree, then run fleet accept demo 01 again.", out)
        evidence = md.read_text()
        self.assertIn("1. `echo combined tail line; exit 3`: FAILED, exit 3", evidence)
        self.assertIn("combined tail line", evidence)
        state = self.state()
        self.assertEqual(state["tickets"]["01"]["status"], "pending")
        self.assertEqual(state["workers"]["01"]["state"], "exited")  # stopped, as a failed gate leaves it
        self.assertEqual(self.status(), "needs-verification")
        out = self.accept("--accept-anyway", OWNER)
        self.assertIn("ACCEPT-CHECKS 01: the owner's words (--accept-anyway) override the failure", out)
        self.assertIn("01: verified", out)
        self.assertIn(f"It overrode: the combined checks failed in the checkout (echo combined tail line; exit 3: "
                      f"exit 3); evidence {md}", self.state()["decisions"][-1])
        report = self.run_file("reports", "01.md").read_text()
        self.assertIn("# Combined checks: 01", report)  # its evidence is part of the writer's
        self.assertIn("## Accepted anyway", report)

    def test_the_owners_words_cover_a_combined_check_failure_only_once_it_was_reported(self):
        """Final review, 6: words given for an open question never cover a failing suite the owner had not seen."""
        self.plan(accept_checks=[{"run": "echo 3 tests FAILED; exit 1"}])
        self.launch()
        self.hand_back(questions=["Should the banner be blue?"])
        out = self.accept("--accept-anyway", "Owner: blue is fine, ship it", expected=1)
        md = self.run_file("evidence", "01.accept-checks.md")
        self.assertIn(f"ACCEPT-CHECKS 01 0/1 passed in the checkout (echo 3 tests FAILED; exit 1: exit 1); evidence {md}",
                      out)
        self.assertIn("The owner's words (--accept-anyway) were given before this failure was reported, so they do "
                      "not cover it: show the owner the failure and its evidence file.", out)
        self.assertNotIn("override the failure", out)
        state = self.state()
        self.assertEqual(state["tickets"]["01"]["status"], "pending")
        self.assertNotIn("accepted_anyway", state["tickets"]["01"])
        self.assertFalse(any("regardless" in d for d in state["decisions"]))
        # Once reported, the owner's words on the same HEAD and tree cover it.
        out = self.accept("--accept-anyway", "Owner, having read the failing suite: ship it")
        self.assertIn("ACCEPT-CHECKS 01: the owner's words (--accept-anyway) override the failure", out)
        overrode = self.state()["tickets"]["01"]["accepted_anyway"]
        self.assertEqual(len(overrode), 2)
        self.assertTrue(overrode[1].startswith("the combined checks failed in the checkout"))

    def test_a_reported_failure_does_not_cover_one_on_a_tree_changed_since(self):
        self.plan(accept_checks=[{"run": "echo 3 tests FAILED; exit 1"}])
        self.launch()
        self.hand_back()
        self.accept(expected=1)  # reported
        (self.repo / "owned.txt").write_text("implemented change, edited again\n")
        out = self.accept("--accept-anyway", OWNER, expected=1)  # the tree moved since: a failure not yet seen
        self.assertIn("were given before this failure was reported", out)
        self.assertIn("ACCEPT-CHECKS 01: the owner's words", self.accept("--accept-anyway", OWNER))

    def test_a_reported_failure_does_not_cover_another_failure_on_the_same_tree(self):
        count = Path(tempfile.mkdtemp()) / "count"
        self.addCleanup(count.unlink, missing_ok=True)
        self.plan(accept_checks=[{"run": f"echo x >> {count}; exit $(wc -l < {count})"}])  # exit 1, then 2, ...
        self.launch()
        self.hand_back()
        self.assertIn("exit 1", self.accept(expected=1))
        out = self.accept("--accept-anyway", OWNER, expected=1)
        self.assertIn("exit 2", out)
        self.assertIn("were given before this failure was reported", out)
        self.assertEqual(self.state()["tickets"]["01"]["status"], "pending")

    def test_a_passing_run_is_reused_while_head_and_the_tree_are_unchanged(self):
        count = Path(tempfile.mkdtemp()) / "count"
        self.addCleanup(count.unlink, missing_ok=True)
        tickets = [{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "files": ["third.txt"]}]
        self.cfg["providers"]["writer"]["max"] = 3
        self.cfg["defaults"]["max_parallel"] = 3
        self.plan(tickets=tickets, accept_checks=[{"run": f"echo ran >> {count}"}])
        for i, f in (("01", "owned.txt"), ("02", "next.txt")):
            self.launch(i)
            self.hand_back(i, file=f)
        out = self.accept()
        self.assertIn("ACCEPT-CHECKS 01 1/1 passed in the checkout; evidence ", out)
        self.assertEqual(count.read_text().count("ran"), 1)
        out = self.accept(ticket="02")  # 02's change was already in the tree the checks passed on
        self.assertIn("ACCEPT-CHECKS 02 reused the passing run at ", out)
        self.assertIn("(accepting 01): HEAD and the tree are unchanged since, so the checks were not run again", out)
        self.assertEqual(count.read_text().count("ran"), 1)
        self.assertIn("Reused: the run's accept_checks passed at ", self.run_file("evidence", "02.accept-checks.md")
                      .read_text())
        self.launch("03")
        self.hand_back("03", file="third.txt")  # the tree moved: they run again
        self.assertIn("ACCEPT-CHECKS 03 1/1 passed in the checkout", self.accept(ticket="03"))
        self.assertEqual(count.read_text().count("ran"), 2)

    def test_what_the_checks_write_outside_every_ticket_is_theirs_while_another_worker_runs(self):
        """#53 review, 2: the checks' own outputs are recorded as the gate records its own, live workers or not."""
        tickets = [{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}]
        self.cfg["providers"]["writer"]["max"] = 2
        self.plan(tickets=tickets, accept_checks=[{"run": "echo cached > checks-cache.txt"}])
        self.launch("01")
        self.launch("02")
        self.hand_back("01")
        out = self.accept()  # 02's worker still runs in this checkout
        self.assertIn("ACCEPT-CHECKS 01 1/1 passed in the checkout", out)
        self.assertIn("01: verified", out)
        self.assertEqual(self.state()["workers"]["02"]["state"], "running")
        self.assertIn("The checks wrote `checks-cache.txt` outside every ticket's files.",
                      self.run_file("evidence", "01.accept-checks.md").read_text())

    def test_what_the_checks_write_while_another_worker_runs_still_counts_for_that_worker(self):
        """Final review, 2: while 02 runs, Fleet cannot tell 02's stray edit from what a long suite writes, so it is
        excused for 01 (stopped then), never for 02, and fleet check lists it."""
        tickets = [{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}]
        self.cfg["providers"]["writer"]["max"] = 2
        self.plan(tickets=tickets, accept_checks=[{"run": "echo 'stray edit by 02' > stray.txt"}])
        self.launch("01")
        self.launch("02")
        self.hand_back("01")
        self.assertIn("01: verified", self.accept())
        md = self.run_file("evidence", "01.accept-checks.md").read_text()
        self.assertIn("02 ran in this checkout meanwhile, and Fleet cannot tell their edits from what the checks wrote, "
                      "so the scope check still counts `stray.txt` for them", md)
        self.hand_back("02", file="next.txt")
        self.stop_with_receipt("02")
        out = self.accept(ticket="02", expected=1)
        self.assertIn("02 not accepted; nothing was stopped or verified", out)
        self.assertIn("stray.txt (created; accept_checks wrote it while 02 ran, so Fleet cannot tell it from its edit)",
                      out)
        self.assertEqual(self.state()["tickets"]["02"]["status"], "pending")
        self.assertIn("stray.txt (created; accept_checks wrote it while 02 ran", self.call("check", "demo"))
        # Once the file is gone, the checks write it again with no worker running: theirs, for everyone.
        (self.repo / "stray.txt").unlink()
        self.assertIn("02: verified", self.accept(ticket="02"))
        self.assertNotIn("NOTE changed since the run started", self.call("check", "demo"))

    def test_an_output_rewritten_later_keeps_the_workers_that_ran_when_it_was_written(self):
        """A third ticket, stopped whenever the checks ran, is never blamed for their output; 02 still is."""
        count = Path(tempfile.mkdtemp()) / "count"
        self.addCleanup(count.unlink, missing_ok=True)
        tickets = [{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "files": ["third.txt"]}]
        self.cfg["providers"]["writer"]["max"] = 3
        self.cfg["defaults"]["max_parallel"] = 3
        self.plan(tickets=tickets, accept_checks=[{"run": f"echo run >> {count}; cat {count} > cache.txt"}])
        self.launch("01")
        self.launch("02")
        self.hand_back("01")
        self.assertIn("01: verified", self.accept())
        self.launch("03")
        self.hand_back("03", file="third.txt")
        self.assertIn("03: verified", self.accept(ticket="03"))  # the checks rewrote cache.txt while 03 was stopped
        self.hand_back("02", file="next.txt")
        self.stop_with_receipt("02")
        self.assertIn("cache.txt (created; accept_checks wrote it while 02 ran", self.accept(ticket="02", expected=1))

    def test_checks_that_change_a_tickets_files_refuse_before_verifying_and_never_blame_the_worker(self):
        """#53 review, 2: a formatter or codegen in accept_checks must not rewrite the code it checks."""
        self.plan(accept_checks=[{"run": "echo reformatted >> owned.txt"}])
        self.launch()
        self.hand_back()
        out = self.accept(expected=1)
        md = self.run_file("evidence", "01.accept-checks.md")
        self.assertIn("01 not accepted: accept_checks changed owned.txt; a check must not modify the code it checks, "
                      "so nothing was verified. This is the checks' doing, not the worker's; their evidence is in "
                      f"{md}.", out)
        self.assertIn("The checks changed `owned.txt`, which tickets own", md.read_text())
        state = self.state()
        self.assertEqual(state["tickets"]["01"]["status"], "pending")
        self.assertFalse(state["workers"]["01"].get("verified"))
        self.assertEqual(self.status(), "needs-verification")
        self.assertIn("The owner's words cannot override this", self.accept("--accept-anyway", OWNER, expected=1))

    def test_a_failed_run_is_never_reused(self):
        self.plan(accept_checks=[{"run": "test -f .scratch/pass"}])
        self.launch()
        self.hand_back()
        self.accept(expected=1)
        (self.repo / ".scratch").mkdir()
        (self.repo / ".scratch" / "pass").write_text("")  # .scratch/ is outside the compared tree
        self.assertIn("ACCEPT-CHECKS 01 1/1 passed in the checkout", self.accept())


class WorktreeChecks(WorktreeCase):
    def test_accept_checks_run_on_the_merged_checkout(self):
        self.cfg["defaults"]["accept_checks"] = [{"run": "grep -q 'from a' a.txt"}]
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "from a\n"}, report=False)
        self.report_path("a").write_text("# Report a\nStatus: needs-verification\n\n```fleet\n" + json.dumps(
            {"files_changed": ["a.txt"], "checks": [], "criteria": [], "findings": [], "questions": []})
            + "\n```\n")
        self.stop_with_receipt("a")
        out = self.accept("a")
        self.assertLess(out.index("MERGED a: applied 1 file(s)"), out.index("ACCEPT-CHECKS a 1/1 passed"))
        self.assertIn("a: verified", out)

    def test_a_failure_after_the_merge_says_the_diff_is_in_the_checkout(self):
        self.cfg["defaults"]["accept_checks"] = [{"run": "echo broken build; exit 1"}]
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "from a\n"}, report=False)
        self.report_path("a").write_text("# Report a\nStatus: needs-verification\n\n```fleet\n" + json.dumps(
            {"files_changed": ["a.txt"], "checks": [], "criteria": [], "findings": [], "questions": []})
            + "\n```\n")
        self.stop_with_receipt("a")
        out = self.accept("a", expected=1)
        self.assertIn("Nothing was verified; its diff is already in the checkout's working tree (MERGED above), and "
                      "the next accept does not apply it again.", out)
        self.assertEqual((self.repo / "a.txt").read_text(), "from a\n")
        self.assertEqual(self.state()["tickets"]["a"]["status"], "pending")

    def test_checks_that_rewrite_the_merged_files_refuse_clearly(self):
        """#53 review, 2: after MERGED, a check that edits the ticket's files is named, not "not in the checkout"."""
        self.cfg["defaults"]["accept_checks"] = [{"run": "echo formatted >> a.txt"}]
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "from a\n"}, report=False)
        self.report_path("a").write_text("# Report a\nStatus: needs-verification\n\n```fleet\n" + json.dumps(
            {"files_changed": ["a.txt"], "checks": [], "criteria": [], "findings": [], "questions": []})
            + "\n```\n")
        self.stop_with_receipt("a")
        out = self.accept("a", expected=1)
        self.assertIn("a not accepted: accept_checks changed a.txt; a check must not modify the code it checks, so "
                      "nothing was verified; its diff is already in the checkout's working tree (MERGED above)", out)
        self.assertNotIn("not in the checkout yet", out)
        self.assertEqual(self.state()["tickets"]["a"]["status"], "pending")
        # Accepting again without putting the file back never records the checks' rewrite as the merge.
        out = self.accept("a", expected=1)
        self.assertIn("CONFLICT a: a.txt", out)
        self.assertIn("changed in the checkout since Fleet last merged this ticket", out)
        self.assertNotIn("ACCEPT-CHECKS", out)
        self.assertEqual(self.state()["tickets"]["a"]["status"], "pending")

    def test_a_merged_file_changed_in_the_checkout_afterwards_is_never_recorded_as_the_merge(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "from a\n"})
        self.stop_with_receipt("a")
        self.call("merge", "demo", "a")
        (self.repo / "a.txt").write_text("from a\nand an edit after the merge\n")
        out = self.call("merge", "demo", "a", expected=1)
        self.assertIn("CONFLICT a: a.txt", out)
        self.assertIn("changed in the checkout since Fleet last merged this ticket", out)
        self.assertNotIn("no changes to apply", out)

    def report_path(self, i):
        return self.repo / RUN / "reports" / f"{i}.md"


class Config(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text(text)
            with contextlib.redirect_stderr(io.StringIO()):
                return fleet.load_config(path)

    def test_the_keys_default_to_strict_and_no_checks(self):
        cfg = self.load("")
        self.assertEqual((cfg["defaults"]["acceptance"], cfg["defaults"]["accept_checks"]), ("strict", []))
        cfg = self.load('[defaults]\nacceptance = "warn"\naccept_checks = [{run = "npm test", timeout = 900}]\n')
        self.assertEqual(cfg["defaults"]["acceptance"], "warn")

    def test_bad_values_are_refused(self):
        for text, message in (
                ('acceptance = "loose"', 'defaults.acceptance must be "strict" (the default) or "warn"'),
                ('accept_checks = "npm test"', "defaults.accept_checks must be a list of commands like"),
                ('accept_checks = [{manual = "eyeball it"}]', "accept_checks take commands only"),
                ('accept_checks = [{run = "x", shell = "bash"}]', "unknown key shell"),
                ('accept_checks = [{run = " "}]', "defaults.accept_checks item 1 needs run"),
                ('accept_checks = [{run = "x", cwd = "../up"}]', "cwd must be a repo-relative directory"),
                ('accept_checks = [{run = "x", timeout = 0}]', "timeout must be whole seconds from 1 to 3600")):
            with self.subTest(text):
                with self.assertRaises(fleet.FleetError) as e:
                    self.load(f"[defaults]\n{text}\n")
                self.assertIn(message, str(e.exception))


class InitChecks(QualityCase):
    def test_init_refuses_a_cwd_that_is_not_a_directory_and_creates_nothing(self):
        self.cfg["defaults"]["accept_checks"] = [{"run": "make", "cwd": "web"}]
        out = self.call("init", "demo", "--workspace", "workspace:1", "--review", "off", *self.off_reason("off"),
                        expected=1)
        self.assertIn("defaults.accept_checks item 1: check cwd 'web' is not a directory in the repository", out)
        self.assertFalse((self.repo / RUN / "run.json").exists())


if __name__ == "__main__":
    unittest.main()
