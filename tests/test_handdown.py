"""A dependent's prompt hands down what happened since the plan: its blockers' verified work, later decisions
and the lead's notes (#23)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402

RUN = ".fleet/runs/demo"


class HandDown(RuntimeCase):
    def prompt_text(self, ticket="02"):
        return (self.repo / RUN / "prompts" / f"{ticket}.md").read_text()

    def deliver_01(self, report):
        """Launch 01, change its file, hand back `report` and verify it."""
        self.launch("01")
        (self.repo / "owned.txt").write_text("owner baseline\nnew interface: parse(text) -> Tree\n")
        (self.repo / RUN / "reports/01.md").write_text(report)
        self.stop_with_receipt("01")
        self.verify("01")

    def test_a_verified_blocker_hands_down_its_changed_files_and_summary_without_the_appendix(self):
        self.setup_run()
        self.deliver_01("# Report 01\n\nStatus: needs-verification\n\n## Work or review performed\n"
                        "- owned.txt: added parse(text) -> Tree\n\n## Appendix\n- appendix-only detail\n")
        self.prompt("02", "--force")
        text = self.prompt_text()
        self.assertIn("## Since this ticket was planned", text)
        self.assertIn("### 01: verified", text)
        self.assertIn("Changed files: owned.txt (changed)", text)
        self.assertIn("> - owned.txt: added parse(text) -> Tree", text)
        self.assertNotIn("appendix-only detail", text)
        section = text.split("## Since")[1].split("## How to work")[0]
        self.assertNotIn("Status:", section)
        self.assertNotIn("Lead verification", text)  # verify appends the lead's evidence; that stays out
        self.assertNotIn("# Report 01", text)
        self.assertEqual(self.state()["prompts"]["02"]["verified_blockers"], ["01"])

    def test_a_long_summary_is_capped_and_points_to_the_full_report(self):
        self.setup_run()
        body = "".join(f"- finding {n}\n" for n in range(60))
        self.deliver_01("# Report 01\nStatus: needs-verification\n\n" + body)
        self.prompt("02", "--force")
        text = self.prompt_text()
        report = self.repo / RUN / "reports/01.md"
        self.assertIn("> - finding 39", text)
        self.assertNotIn("finding 40", text)
        self.assertIn(f"20 more lines cut; read the rest in `{report}`.", text)

    def test_an_unverified_blocker_is_named_as_such(self):
        self.setup_run()
        text = self.prompt_text()
        self.assertIn("### 01: not verified yet", text)
        self.assertNotIn("Changed files", text)
        self.assertEqual(self.state()["prompts"]["02"]["verified_blockers"], [])

    def test_an_investigate_blocker_hands_down_only_its_report(self):
        self.setup_run(tickets=[{"id": "q", "kind": "investigate"},
                                {"id": "02", "files": ["next.txt"], "blockers": ["q"]}])
        self.launch("q")
        (self.repo / RUN / "reports/q.md").write_text("# Report q\nStatus: needs-verification\n\n"
                                                      "## Findings\n- the cache key ignores locale\n")
        self.stop_with_receipt("q")
        self.verify("q")
        self.prompt("02", "--force")
        text = self.prompt_text()
        self.assertIn("### q: verified", text)
        self.assertIn("> - the cache key ignores locale", text)
        self.assertNotIn("Changed files", text)

    def test_later_decisions_appear_and_plan_time_ones_do_not(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        add = self.repo / "add.json"
        add.write_text(json.dumps({"tickets": [{"id": "03", "files": ["third.txt"], "task": "Write the third file.",
                                                "provider": "writer", "tier": "standard"}],
                                   "decisions": ["Owner chose JSON over YAML."]}))
        self.call("add", "demo", "--file", str(add))
        self.call("drop", "demo", "03", "--reason", "owner changed their mind")
        self.prompt("01", "--force")
        text = self.prompt_text("01")
        self.assertIn("### Decisions since the plan", text)
        self.assertIn("- Owner chose JSON over YAML. (added ", text)
        self.assertIn("- Dropped 03: owner changed their mind", text)
        self.assertNotIn("Use bounded local checks.", text)

    def test_a_run_planned_before_the_count_falls_back_to_stamped_decisions(self):
        state = {"decisions": ["Use bounded local checks.", "Owner chose JSON (added 2026-01-02T03:04:05)",
                               "Dropped 03: no longer needed (2026-01-02T03:05:00)"]}
        self.assertEqual(fleet.later_decisions(state), state["decisions"][1:])

    def test_the_leads_notes_are_pointed_to(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        self.assertNotIn("Since this ticket was planned", self.prompt_text("01"))
        notes = self.repo / RUN / "notes.md"
        notes.write_text("- Owner wants British spelling.\n")
        self.prompt("01", "--force")
        self.assertIn(f"`{notes}` holds the lead's later decisions", self.prompt_text("01"))

    def test_nothing_to_hand_down_means_no_section(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        text = self.prompt_text("01")
        self.assertNotIn("Since this ticket was planned", text)
        self.assertNotIn("{{handdown}}", text)
        self.assertIn("**Checks to run:** Lead:", text)

    def test_reviewer_prompts_are_unchanged(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"]}])
        (self.repo / RUN / "notes.md").write_text("- a later decision\n")
        self.launch("01")
        (self.repo / "owned.txt").write_text("changed\n")
        self.hand_back("01")
        self.review_prompt()
        text = self.prompt_text("review-01")
        self.assertNotIn("Since this ticket was planned", text)
        self.assertNotIn("verified_blockers", self.state()["prompts"]["review-01"])

    def test_launch_warns_about_a_prompt_written_before_a_blocker_was_verified(self):
        self.setup_run()  # 02's prompt is written while 01 is unverified
        self.deliver_01("# Report 01\nStatus: needs-verification\n\n- done\n")
        out = self.launch("02")
        self.assertIn("02's prompt was written before 01 was verified", out)
        self.assertIn("fleet prompt demo 02 --force", out)
        self.assertEqual(self.state()["workers"]["02"]["state"], "running")  # a warning, not a refusal

    def test_launch_is_quiet_once_the_prompt_is_regenerated(self):
        self.setup_run()
        self.deliver_01("# Report 01\nStatus: needs-verification\n\n- done\n")
        self.prompt("02", "--force")
        self.assertNotIn("was verified", self.launch("02"))

    def test_launch_ready_writes_a_current_prompt(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "context": "c", "checks": "k"},
                                {"id": "02", "files": ["next.txt"], "blockers": ["01"], "context": "c",
                                 "checks": "k"}])
        state = self.state()
        del state["prompts"]["02"]  # as if the plan alone had been saved: --ready writes 02's prompt itself
        (self.repo / RUN / "run.json").write_text(json.dumps(state))
        (self.repo / RUN / "prompts/02.md").unlink()
        self.deliver_01("# Report 01\nStatus: needs-verification\n\n- parse() is in owned.txt\n")
        out = self.call("launch", "demo", "--ready")
        self.assertIn("LAUNCHED 02", out)
        self.assertNotIn("was verified", out)
        self.assertIn("> - parse() is in owned.txt", self.prompt_text())

    def test_a_prompt_from_an_earlier_fleet_gets_no_warning(self):
        self.setup_run()
        state = self.state()
        del state["prompts"]["02"]["verified_blockers"]
        (self.repo / RUN / "run.json").write_text(json.dumps(state))
        self.deliver_01("# Report 01\nStatus: needs-verification\n\n- done\n")
        self.assertNotIn("was verified", self.launch("02"))

    def test_a_lead_comment_quoted_from_a_blockers_report_does_not_block_launch(self):
        self.setup_run(tickets=[{"id": "01", "files": ["owned.txt"], "context": "Spec.", "checks": "Read it."},
                                {"id": "02", "files": ["next.txt"], "blockers": ["01"], "context": "Spec.",
                                 "checks": "Read it."}])
        self.launch()
        (self.repo / "owned.txt").write_text("implemented\n")
        report = self.repo / ".fleet/runs/demo/reports/01.md"
        report.write_text("# Report 01\n\nStatus: needs-verification\n\n## Work or review performed\n"
                          "- templates/ticket.md: kept the `<!-- LEAD: fill me -->` marker as the spec asks\n")
        self.stop_with_receipt()
        self.verify()
        out = self.call("prompt", "demo", "02", "--force")
        self.assertNotIn("fill before launch", out)
        self.assertIn("<!-- LEAD: fill me -->", (self.repo / ".fleet/runs/demo/prompts/02.md").read_text())
        self.launch("02")

