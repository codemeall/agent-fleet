"""Review rounds: past defaults.review_rounds reviewer launches, the next repair or review of a ticket waits for
the owner, who sees the last reviewer's open findings and may allow exactly one more round."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


FINDING = "- High, owned.txt:1: the 404 case returns an error instead of null; return null and test it."


class ReviewRounds(RuntimeCase):
    def quits(self):
        """Stops made inside review and accept write the exit receipt, as in test_review_accept."""
        def quit_keys(workspace, surface, items):
            for i, w in self.state()["workers"].items():
                if surface in (w.get("surface_id"), w["surface"]):
                    self.receipt(i)
        return patch.object(fleet, "send_keys", side_effect=quit_keys)

    def review(self, *args, expected=0):
        with self.quits():
            return self.call("review", "demo", "01", *args, expected=expected)

    def findings(self, lines=(FINDING,)):
        """The reviewer hands back with findings, and stops."""
        path = self.repo / ".fleet/runs/demo/reports/review-01.md"
        path.write_text("# Report review-01\n\nStatus: needs-verification\n\n## Findings\n" + "\n".join(lines) +
                        "\n\n## Decisions and blockers\n- none\n")
        self.stop_with_receipt("review-01")

    def repair(self, change, *args, expected=0):
        out = self.call("launch", "demo", "01", "writer", "--tier", "standard", *args, expected=expected)
        if expected == 0:
            (self.repo / "owned.txt").write_text(change)
            self.report("01")
        return out

    def two_rounds(self):
        self.setup_run(review="cross-all")
        self.repair("first change\n")
        self.review()
        self.findings()
        self.repair("second change\n")
        self.review()
        self.findings()

    def rounds(self):
        return self.state()["tickets"]["01"].get("review_rounds")

    def test_the_third_repair_and_the_third_review_are_refused_with_the_escalation(self):
        self.two_rounds()
        self.assertEqual(self.rounds(), 2)
        out = self.repair("third change\n", expected=1)
        self.assertIn("01 has used 2 of 2 review rounds, so Fleet refuses another repair. Nothing was changed.", out)
        self.assertIn("ESCALATE 01: 2 of 2 review rounds used", out)
        self.assertIn("review-01's open findings, quoted from .fleet/runs/demo/reports/review-01.md", out)
        self.assertIn("> " + FINDING, out)
        self.assertNotIn("Decisions and blockers", out)                  # only the Findings section is quoted
        self.assertIn("- accept as is: fleet accept demo 01 --evidence <file>", out)
        self.assertIn("- drop the ticket: fleet drop demo 01 --reason", out)
        self.assertIn('--extra-round "<owner\'s words>"', out)
        self.assertEqual(self.state()["workers"]["01"]["state"], "exited")  # nothing launched
        out = self.review(expected=1)
        self.assertIn("refuses another review", out)
        self.assertEqual(self.state()["workers"]["review-01"]["state"], "exited")
        self.assertEqual(self.rounds(), 2)

    def test_an_extra_round_allows_exactly_one_more_and_is_recorded(self):
        self.two_rounds()
        self.assertIn("needs the owner's words", self.repair("x\n", "--extra-round", "  ", expected=1))
        self.repair("third change\n", "--extra-round", "Owner: one more try at the 404 case")
        decisions = self.state()["decisions"]
        self.assertTrue(any(d.startswith("Extra review round for 01 (round 3): Owner: one more try at the 404 case (")
                            for d in decisions), decisions)
        # The round it allowed is not banked twice: the review that follows needs no flag, and refuses one.
        self.assertIn("used 2 of 3 review rounds, so another review needs no extra round; drop --extra-round",
                      self.review("--extra-round", "again", expected=1))
        self.assertIn("REVIEW review-01", self.review())
        self.assertEqual(self.rounds(), 3)
        self.findings()
        self.assertIn("used 3 of 3 review rounds", self.repair("fourth change\n", expected=1))
        self.assertIn("rounds=3/3", self.call("resume", "demo"))

    def test_review_takes_an_extra_round_itself_and_counts_it_once(self):
        self.two_rounds()
        out = self.review("--extra-round", "Owner: review the unchanged diff once more")
        self.assertIn("REVIEW review-01", out)
        state = self.state()
        self.assertEqual((state["tickets"]["01"]["review_rounds"], state["tickets"]["01"]["extra_rounds"]), (3, 1))
        self.assertEqual(sum(d.startswith("Extra review round for 01") for d in state["decisions"]), 1)

    def test_resume_shows_rounds_used_against_the_cap(self):
        self.setup_run(review="cross-all")
        self.assertIn("rounds=0/2", self.call("resume", "demo"))
        self.repair("first change\n")
        self.review()
        self.assertIn("rounds=1/2", self.call("resume", "demo"))

    def test_a_long_form_review_launch_counts_and_the_cap_comes_from_config(self):
        self.cfg["defaults"]["review_rounds"] = 1
        self.setup_run(review="cross-all")
        self.repair("first change\n")
        self.stop_with_receipt("01")
        self.review_prompt()
        self.call("launch", "demo", "review-01", "reviewer", "--tier", "review")
        self.assertEqual(self.rounds(), 1)
        self.findings()
        self.assertIn("used 1 of 1 review rounds", self.repair("x\n", expected=1))
        out = self.call("launch", "demo", "review-01", "reviewer", "--tier", "review", expected=1)
        self.assertIn("refuses another review", out)

    def test_an_accepted_ticket_is_unaffected_by_the_cap(self):
        self.two_rounds()
        with self.quits():
            out = self.call("accept", "demo", "01", "--evidence", str(self.evidence))
        self.assertIn("01: verified", out)

    def test_findings_are_capped_in_the_escalation(self):
        self.cfg["defaults"]["review_rounds"] = 1
        self.setup_run(review="cross-all")
        self.repair("first change\n")
        self.review()
        self.findings([f"- finding {n} " + "x" * 300 for n in range(30)])
        out = self.repair("x\n", expected=1)
        self.assertIn("> - finding 19 ", out)
        self.assertNotIn("finding 20", out)
        self.assertIn("10 more lines cut; read the rest in .fleet/runs/demo/reports/review-01.md", out)
        self.assertNotIn("x" * 250, out)

    def test_a_run_from_before_rounds_were_counted_counts_the_reviewers_it_can_see(self):
        self.setup_run(review="cross-all")
        self.repair("first change\n")
        self.review()
        self.findings()
        path = self.repo / ".fleet/runs/demo/run.json"
        old = json.loads(path.read_text())
        del old["tickets"]["01"]["review_rounds"]
        del old["options"]["review_rounds"]
        path.write_text(json.dumps(old))
        self.assertIn("rounds=1/2", self.call("resume", "demo"))  # the cap comes from the current config
        self.repair("second change\n")
        self.review()
        self.assertEqual(self.rounds(), 2)

    def test_launch_ready_takes_no_extra_round(self):
        self.setup_run(review="cross-all")
        out = self.call("launch", "demo", "--ready", "--extra-round", "why", expected=1)
        self.assertIn("drop --extra-round", out)


    def test_resuming_a_reviewer_is_the_same_round(self):
        reviewer = self.cfg["providers"]["reviewer"]
        reviewer.update(launch="{bin} {session} --model {model} --effort {effort} {prompt}",
                        session="--session-id {id}", resume="--resume {id}")
        self.setup_run(review="cross-all")
        self.repair("first change\n")
        self.review()
        self.stop_with_receipt("review-01")                               # a usage limit, say
        self.call("launch", "demo", "review-01", "reviewer", "--tier", "review", "--resume")
        self.assertEqual(self.rounds(), 1)


class ReviewRoundsConfig(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.toml"
            path.write_text(f"[defaults]\nreview_rounds = {value}\n")
            return fleet.load_config(path)

    def test_review_rounds_is_a_whole_number_from_one_to_five(self):
        self.assertEqual(fleet.load_config(Path("/nonexistent/config.toml"))["defaults"]["review_rounds"], 2)
        for good in (1, 5):
            self.assertEqual(self.load(good)["defaults"]["review_rounds"], good)
        for bad in ("0", "6", "true", '"2"', "2.0"):
            with self.assertRaisesRegex(fleet.FleetError, "review_rounds must be a whole number from 1 to 5"):
                self.load(bad)


if __name__ == "__main__":
    unittest.main()
