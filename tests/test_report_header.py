"""The report header: a `fleet` block of JSON facts that Fleet checks against the run and summarizes on the REPORT
line, so the lead reads a report's prose only when something is off (#29)."""
import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import ROOT, RuntimeCase, fleet  # noqa: E402

RUN = ".fleet/runs/demo"


def header(**fields) -> str:
    data = {"files_changed": ["owned.txt"], "checks": [{"run": "true", "exit": 0}],
            "criteria": [{"id": 1, "met": True, "where": "owned.txt"}, {"id": 2, "met": True, "where": "true"}],
            "findings": [], "questions": []}
    data.update(fields)
    return "```fleet\n" + json.dumps(data) + "\n```\n"


def report(block: str, status="needs-verification") -> str:
    return (f"# Report\n\nStatus: {status}\n\n{block}\n## Work or review performed\n- owned.txt: the change\n\n"
            "## Appendix\n- detail\n")


class ReportHeader(RuntimeCase):
    def setup_checked(self, **ticket):
        """One implement ticket with a planned command and a two-checkbox source ticket, launched."""
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "checks": [{"run": "true"}, "read the diff"],
                                 **ticket}])
        (self.repo / "01.md").write_text("# Ticket 01\n- [ ] first criterion\n- [x] second criterion\n")
        self.launch()

    def write(self, text, ticket="01"):
        path = self.repo / RUN / "reports" / f"{ticket}.md"
        path.write_text(text)
        return path

    def wake(self, text, ticket="01"):
        path = self.write(text, ticket)
        out = self.call("wait", "demo", "--timeout", "0", "--settle", "0")
        line = next(x for x in out.splitlines() if x.startswith(f"REPORT {ticket} "))
        self.assertIn(str(path), line)
        return line

    def split_call(self, *args, expected=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(fleet.main(list(args)), expected, out.getvalue() + err.getvalue())
        return out.getvalue(), err.getvalue()

    # ---------- the template ----------

    def test_the_template_documents_the_block_with_one_valid_example_and_every_prompt_kind_carries_it(self):
        template = (ROOT / "skill/templates/report.md").read_text()
        self.assertEqual(template.count("```fleet\n"), 1)
        example = template.split("```fleet\n")[1].split("```")[0]
        data, problems = fleet.parse_header("```fleet\n" + example + "```\n")
        self.assertEqual(problems, [])
        self.assertEqual(set(data), set(fleet.HEADER_SHAPES))
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "kind": "investigate"}])
        self.launch()
        (self.repo / "owned.txt").write_text("changed\n")
        self.hand_back()
        self.review_prompt()
        for i in ("01", "02", "review-01"):
            with self.subTest(prompt=i):
                self.assertIn("```fleet\n", (self.repo / RUN / "prompts" / f"{i}.md").read_text())

    # ---------- the REPORT line ----------

    def test_a_valid_header_puts_the_summary_between_status_and_path(self):
        self.setup_checked()
        path = self.repo / RUN / "reports/01.md"
        line = self.wake(report(header()))
        self.assertEqual(line, f"REPORT 01 [needs-verification] criteria 2/2, checks 1/1 exit 0, findings 0, "
                               f"questions 0, files in scope {path}  (read lines 1-11 of 13; appendix below)")

    def test_unmet_criteria_nonzero_exits_and_open_questions_are_counted(self):
        self.setup_checked()
        line = self.wake(report(header(
            criteria=[{"id": 1, "met": False, "where": "x"}, {"id": 2, "met": True, "where": "y"}],
            checks=[{"run": "true", "exit": 0}, {"run": "make lint", "exit": 2}, {"run": "by hand", "exit": None}],
            questions=["Which copy wins?", ""])))
        self.assertIn("criteria 1/2, checks 1/3 exit 0, findings 0, questions 1, files in scope ", line)
        self.assertNotIn("header", line)

    def test_each_problem_with_the_run_is_named(self):
        self.setup_checked()
        cases = [
            (header(files_changed=["owned.txt", "./src/x.py"]), "files_changed outside the scope",
             "header: src/x.py outside scope"),
            (header(checks=[]), "a planned command left out", "checks none, findings 0, questions 0, files in scope, "
                                                              "header: true not reported"),
            (header(checks=[{"run": "true", "exit": None}]), "a planned command without an exit",
             "header: true has no exit code"),
            (header(criteria=[{"id": 1, "met": True, "where": "x"}]), "fewer criteria than checkboxes",
             "header: 1 criteria for the ticket's 2 checkboxes"),
        ]
        for block, why, want in cases:
            with self.subTest(why):
                line = self.wake(report(block))
                self.assertIn(want, line)
                self.assertTrue(line.startswith("REPORT 01 [needs-verification] criteria "), line)
        line = self.wake(report(header(files_changed=["src/x.py"], checks=[])))
        self.assertIn("header: src/x.py outside scope; true not reported ", line)  # several problems, one phrase
        self.assertNotIn("files in scope", line)

    def test_a_malformed_header_is_named_instead_of_a_summary(self):
        self.setup_checked()
        cases = [
            ("```fleet\n{\"files_changed\": [\n```\n", "header: invalid JSON"),
            ("```fleet\n[]\n```\n", "header: not a JSON object"),
            ("```fleet\n{\"files_changed\": []}\n", "header: fleet block not closed"),
            (header(questions="none"), "header: questions must be a list"),
            (header(findings=[{"severity": "critical", "file": "a.py", "confidence": "confirmed"}]),
             "header: findings item 1 is not"),
            (header(checks=[{"run": "true", "exit": True}]), "header: checks item 1 is not"),
            ("```fleet\n{\"files_changed\": []}\n```\n", "header: checks missing; criteria missing; findings missing"),
        ]
        for block, want in cases:
            with self.subTest(want):
                line = self.wake(report(block))
                self.assertTrue(line.startswith("REPORT 01 [needs-verification] " + want), line)

    def test_a_reviewers_line_shows_findings_by_severity_and_needs_no_planned_commands(self):
        self.setup_checked(blockers=[])
        (self.repo / "owned.txt").write_text("changed\n")
        self.hand_back()
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        findings = [{"severity": s, "file": "owned.txt:1", "confidence": "suspected"} for s in ("low", "high", "low")]
        line = self.wake(report(header(files_changed=[], checks=[], findings=findings)), "review-01")
        self.assertIn("criteria 2/2, checks none, findings 3 (1 high, 2 low), questions 0, files none ", line)
        line = self.wake(report(header(findings=findings)), "review-01")
        self.assertIn("header: files_changed lists 1; a review changes none", line)

    def test_an_investigator_lists_no_files(self):
        self.setup_run(tickets=[{"id": "01", "kind": "investigate"}])
        self.launch()
        self.assertIn("files none ", self.wake(report(header(files_changed=[], checks=[]))))
        self.assertIn("header: files_changed lists 1; an investigation changes none",
                      self.wake(report(header(checks=[]))))

    def test_a_report_without_a_header_wakes_exactly_as_before(self):
        self.setup_checked()
        path = self.repo / RUN / "reports/01.md"
        self.assertEqual(self.wake(report("")), f"REPORT 01 [needs-verification] {path}  "
                                                "(read lines 1-8 of 10; appendix below)")
        self.assertEqual(self.wake("# Report\nStatus: blocked\n\nWhich copy?\n"), f"REPORT 01 [blocked] {path}")

    # ---------- verify, accept and the hand-down ----------

    def test_verify_warns_about_header_problems_on_stderr_and_still_verifies(self):
        self.cfg["defaults"]["acceptance"] = "warn"  # the warn policy: header problems warn and never refuse
        self.setup_checked()
        (self.repo / "owned.txt").write_text("changed\n")
        self.write(report(header(files_changed=["src/x.py"])))
        self.stop_with_receipt()
        out, err = self.split_call("verify", "demo", "01", "--evidence", str(self.evidence))
        self.assertIn("01: verified", out)
        self.assertNotIn("WARNING", out)
        self.assertIn("WARNING 01 header: src/x.py outside scope (not a refusal", err)
        self.assertEqual(self.state()["tickets"]["01"]["status"], "verified")

    def test_verify_names_a_missing_header_and_is_silent_for_a_good_one(self):
        self.cfg["defaults"]["acceptance"] = "warn"  # the warn policy: header problems warn and never refuse
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]}])
        for i, text in (("01", report("")), ("02", report(header(files_changed=["next.txt"], checks=[])))):
            self.launch(i)
            self.write(text, i)
            self.stop_with_receipt(i)
        self.assertIn("WARNING 01 header missing", self.split_call("verify", "demo", "01", "--evidence",
                                                                   str(self.evidence))[1])
        self.assertEqual(self.split_call("verify", "demo", "02", "--evidence", str(self.evidence))[1], "")

    def test_accept_warns_for_the_reviewer_and_the_writer_and_still_accepts(self):
        self.cfg["defaults"]["acceptance"] = "warn"  # the warn policy: header problems warn and never refuse
        self.setup_checked(blockers=[])
        (self.repo / "owned.txt").write_text("changed\n")
        self.write(report(header(checks=[])))
        self.stop_with_receipt()
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        self.write(report("```fleet\nnot json\n```\n"), "review-01")
        self.stop_with_receipt("review-01")
        with patch.object(fleet, "send_keys"):
            out, err = self.split_call("accept", "demo", "01", "--evidence", str(self.evidence))
        self.assertIn("WARNING review-01 header: invalid JSON", err)
        self.assertIn("WARNING 01 header: true not reported", err)
        self.assertIn("UNBLOCKED", out)
        self.assertEqual(self.state()["tickets"]["01"]["status"], "verified")

    def test_the_hand_down_summary_leaves_out_the_header(self):
        text = report(header(questions=["machine-only"]))
        summary = "\n".join(fleet.report_summary(text))
        self.assertNotIn("machine-only", summary)
        self.assertNotIn("```", summary)
        self.assertTrue(summary.startswith("## Work or review performed"), summary)
        unclosed = "# R\nStatus: needs-verification\n```fleet\n{}\n- prose a broken fence must not swallow\n"
        self.assertIn("- prose a broken fence must not swallow", fleet.report_summary(unclosed))

    def test_the_workers_own_report_in_files_changed_is_not_a_header_problem(self):
        self.setup_checked()
        line = self.wake(report(header(files_changed=["owned.txt", f"{RUN}/reports/01.md"])))
        self.assertIn("files in scope", line)
        self.assertNotIn("header:", line)

