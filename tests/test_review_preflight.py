"""Init, plan and add refuse a required cross-family review the allowed pool could never give (ticket #9)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_runtime import RuntimeCase, fleet  # noqa: E402


class ReviewPreflight(RuntimeCase):
    def init(self, *args, expected=0):
        return self.call("init", "demo", "--workspace", "workspace:1", *args, expected=expected)

    def plan_file(self, *tiers, name="plan.json"):
        """A plan file with one ticket per tier, each owning its own file, all written by `writer`."""
        tickets = []
        for n, tier in enumerate(tiers, 1):
            i = f"{name.split('.')[0]}-{n}"
            path = self.repo / f"{i}.md"
            path.write_text(f"# Ticket {i}\nImplement the change.\n")
            tickets.append({"id": i, "ticket": str(path), "provider": "writer", "tier": tier, "files": [f"{i}.txt"]})
        path = self.repo / name
        path.write_text(json.dumps({"tickets": tickets, "decisions": ["Agreed plan."]}))
        return str(path)

    def plan(self, *tiers, expected=0):
        return self.call("plan", "demo", "--file", self.plan_file(*tiers), expected=expected)

    def turn_off_reviewer(self):
        self.cfg["providers"]["reviewer"]["enabled"] = False

    def assert_ways_out(self, out):
        self.assertIn("fleet provider enable <name>", out)
        self.assertIn("--routing", out)
        self.assertIn("--review off --reason", out)

    # ---------- init ----------

    def test_init_refuses_cross_all_when_the_pool_holds_one_family(self):
        out = self.init("--routing", "single:writer", "--review", "cross-all", expected=1)
        self.assertIn("review cross-all needs a reviewer whose family differs from every writer's (openai), "
                      "and routing single:writer cannot supply one", out)
        self.assertIn("writer: same family as the writer (openai)", out)
        self.assertIn("No run was created", out)
        self.assert_ways_out(out)
        self.assertFalse((self.repo / ".fleet/runs/demo").exists())

    def test_init_warns_under_cross_heavy_and_passes_with_a_mixed_pool(self):
        out = self.init("--routing", "single:writer")
        self.assertIn("note: plan will refuse heavy implementation tickets", out)
        self.call("init", "other", "--workspace", "workspace:1", "--review", "cross-all")  # auto: two families

    def test_init_leaves_a_mixed_adapter_to_plan(self):
        """A mixed adapter's writer could run any family, so only the plan knows whether review is possible."""
        self.cfg["providers"]["writer"]["family"] = "mixed"
        self.init("--routing", "single:writer", "--review", "cross-all")
        out = self.plan("standard", expected=1)
        self.assertIn("plan-1: review cross-all needs a reviewer whose family differs from the writer's (openai)", out)

    # ---------- plan and add ----------

    def test_plan_refuses_heavy_tickets_a_single_family_pool_cannot_review(self):
        self.turn_off_reviewer()
        self.init()
        out = self.plan("heavy", "standard", "heavy", expected=1)
        self.assertIn("plan-1, plan-3: review cross-heavy needs a reviewer whose family differs from the writer's "
                      "(openai), and routing auto cannot supply one", out)
        self.assertIn("  writer: same family as the writer (openai)", out)
        self.assertNotIn("  reviewer:", out)  # like fleet review, auto tries only providers that are on
        self.assertIn("Nothing was saved", out)
        self.assert_ways_out(out)
        self.assertEqual(self.state()["tickets"], {})
        self.plan("standard", "light")  # no review required, so the same pool is fine

    def test_plan_passes_heavy_tickets_with_a_mixed_pool(self):
        self.init()
        self.plan("heavy", "standard")
        tickets = self.state()["tickets"]
        self.assertTrue(tickets["plan-1"]["review_required"])
        self.assertFalse(tickets["plan-2"]["review_required"])

    def test_plan_uses_the_reviewers_own_checks_not_just_its_family(self):
        """A reviewer whose review tier model is outside models_allow could never launch."""
        self.cfg["providers"]["reviewer"]["models_allow"] = ["other-model"]
        del self.cfg["providers"]["reviewer"]["tiers"]["review"]
        self.init("--review", "cross-all")
        out = self.plan("standard", expected=1)
        self.assertIn("reviewer: no model: pass --model or define tiers.review", out)

    def test_plan_refuses_a_writer_of_unknown_family_when_review_is_required(self):
        writer = self.cfg["providers"]["writer"]
        writer["family"] = "mixed"
        for tier in writer["tiers"].values():
            del tier["family"]
        self.init()
        out = self.plan("heavy", expected=1)
        self.assertIn("plan-1: writer:writer-model has no known family, so its required review (review cross-heavy) "
                      "could never launch", out)
        self.plan("standard")

    def test_add_refuses_a_heavy_ticket_the_pool_cannot_review(self):
        self.init("--routing", "single:writer")
        self.plan("standard")
        out = self.call("add", "demo", "--file", self.plan_file("heavy", name="more.json"), expected=1)
        self.assertIn("more-1: review cross-heavy needs a reviewer whose family differs from the writer's (openai)",
                      out)
        self.assertEqual(list(self.state()["tickets"]), ["plan-1"])
        self.call("add", "demo", "--file", self.plan_file("light", name="more.json"))

    def test_review_route_is_unchanged_by_the_shared_check(self):
        """fleet review's picker still adds capacity on top of the plan-time test."""
        self.init()
        state = self.state()
        why, route = fleet.review_route(self.cfg, state, None, "reviewer", "openai", running=[])
        self.assertIsNone(why)
        self.assertEqual(route["provider"], "reviewer")
        busy = [{"provider": "reviewer"}]
        self.assertIn("no free slot", fleet.review_route(self.cfg, state, None, "reviewer", "openai", busy)[0])
        self.assertIsNone(fleet.review_gap(self.cfg, "auto", None, "openai"))

    # ---------- review off ----------

    def test_review_off_needs_the_owners_reason_and_records_it(self):
        out = self.init("--review", "off", expected=1)
        self.assertIn("--review off drops the config's review cross-heavy", out)
        self.assertIn('--reason "<owner\'s words>"', out)
        self.assertFalse((self.repo / ".fleet/runs/demo").exists())
        self.init("--review", "off", "--reason", "  ", expected=1)
        self.turn_off_reviewer()
        self.init("--review", "off", "--reason", "Owner said no cross review for this docs run")
        decision = "Review off (config default: cross-heavy), by the owner: Owner said no cross review for this docs run"
        self.assertEqual(self.state()["decisions"], [decision])
        self.plan("heavy")  # off needs no reviewer, and the plan keeps init's decision ahead of its own
        state = self.state()
        self.assertEqual(state["decisions"], [decision, "Agreed plan."])
        self.assertEqual(state["plan_decisions"], 2)
        self.assertFalse(state["tickets"]["plan-1"]["review_required"])

    def test_reason_only_goes_with_review_off_and_off_by_config_needs_none(self):
        out = self.init("--review", "cross-all", "--reason", "why not", expected=1)
        self.assertIn("pass it only with --review off", out)
        self.cfg["defaults"]["review"] = "off"
        self.init("--review", "off")
        self.assertEqual(self.state()["decisions"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()
