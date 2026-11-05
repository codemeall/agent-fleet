"""Warm workers (#42, TEAM-REVIEW P18): `fleet launch --after <verified-id>` and a plan's `after` hand a light or
standard ticket to the CLI session that did a verified ticket, through the adapter's `resume`. Command-level tests
use RuntimeCase's fake cmux; the end-to-end tests run the shipped Claude adapters in headless tmux against the
strict fake CLI. No real CLI, account or network is used."""
import json
import os
import re
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from test_runtime import RuntimeCase, fleet  # noqa: E402
from fleet_harness import HAS_TMUX, FleetCase  # noqa: E402
from test_adapters_smoke import LEAD_ACCOUNTS, SHIPPED, install_fakes  # noqa: E402

RESUMABLE = "{bin} {session} --model {model} --effort {effort} {profile} {settings} {prompt}"
REAL_LOAD_CONFIG = fleet.load_config  # RuntimeCase patches it per test


class WarmCase(RuntimeCase):
    """The writer adapter as a Claude account: named sessions, hooks, logs under its own CLAUDE_CONFIG_DIR."""

    def setUp(self):
        super().setUp()
        # Written before the quality bar: these tests cover verify and accept's mechanics with header-less
        # reports, so their runs start under acceptance = "warn". tests/test_quality_bar.py covers "strict".
        self.cfg["defaults"]["acceptance"] = "warn"
        home = tempfile.TemporaryDirectory()  # outside the repo, so its logs are no change in any ticket's scope
        self.addCleanup(home.cleanup)
        self.claude_home = Path(home.name)
        self.cfg["providers"]["writer"].update(
            launch=RESUMABLE, session="--session-id {id}", resume="--resume {id}", hooks="claude-settings",
            account_env="CLAUDE_CONFIG_DIR", env={"CLAUDE_CONFIG_DIR": str(self.claude_home)})

    def run_path(self, *parts):
        return self.repo.joinpath(".fleet/runs/demo", *parts)

    def plan(self, tickets, review="off", isolation=None, prompts=True):
        args = ["init", "demo", "--workspace", "workspace:1", "--review", review, *self.off_reason(review)]
        self.call(*args, *(["--isolation", isolation] if isolation else []))
        self.call("plan", "demo", "--file", self.plan_file(tickets))
        if prompts:
            for t in tickets:
                self.prompt(t["id"])

    def plan_file(self, tickets):
        for t in tickets:
            t.setdefault("provider", "writer")
            t.setdefault("tier", "standard")
            t.setdefault("task", f"Ticket {t['id']}.")
            t.setdefault("context", "Agreed scope only.")
            t.setdefault("checks", {"manual": "Read the diff."})
        path = self.repo / ".scratch" / "plan.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"tickets": tickets}))
        return str(path)

    def two(self, second=None, **plan):
        """01, then 02 in another file; 02 may follow 01 or carry other fields."""
        self.plan([{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"], **(second or {})}], **plan)

    def finish(self, ticket="01"):
        """Launch, hand back, confirm the exit and verify: a ticket whose session a later one may continue."""
        tier = self.state()["tickets"][ticket]["tier"]
        self.launch(ticket, tier=tier)
        self.report(ticket)
        self.stop_with_receipt(ticket)
        self.verify(ticket)
        return self.state()["workers"][ticket]

    def warm(self, ticket="02", after="01", provider="writer", tier="standard", expected=0, *extra):
        return self.call("launch", "demo", ticket, provider, "--tier", tier, "--after", after, *extra,
                         expected=expected)

    def command(self, ticket="02"):
        """The worker's command line from the CLI's name on, after the adapter's CLAUDE_CONFIG_DIR prefix."""
        argv = shlex.split(self.state()["workers"][ticket]["command"])
        return argv[argv.index("unused-test-agent"):]


class Launch(WarmCase):
    def test_after_resumes_the_earlier_session_with_the_new_tickets_line_and_scope(self):
        self.two()
        first = self.finish()
        report, evidence = self.run_path("reports", "01.md"), sorted(self.run_path("evidence").iterdir())
        kept = (report.read_bytes(), [f.read_bytes() for f in evidence])
        self.warm()
        w = self.state()["workers"]["02"]
        self.assertEqual(w["session"], first["session"])
        argv = self.command()
        self.assertEqual(argv[1:3], ["--resume", first["session"]])
        self.assertNotIn("--session-id", argv)
        self.assertEqual(argv[-1], fleet.warm_prompt_line(self.run_path("prompts", "02.md"), "02", "01",
                                                          self.run_path("reports", "02.md"),
                                                          self.run_path("inbox", "02.md")))
        self.assertIn("Fleet ticket 01 is closed", argv[-1])
        self.assertIn("its rules and files replace 01's", argv[-1])
        self.assertEqual(argv[argv.index("--settings") + 1], str(self.run_path("settings", "02.json")))
        scope = json.loads(Path(w["hooks"]["scope"]).read_text())
        self.assertEqual((scope["id"], scope["files"]), ("02", ["next.txt"]))
        self.assertEqual(scope["report"], str(self.run_path("reports", "02.md")))
        self.assertEqual((w["warm_after"], w["warm_chain"]), ("01", 2))
        e = self.state()["ledger"][-1]
        self.assertEqual((e["ticket"], e["warm_after"], e["warm_chain"], e["resume"], e["session"]),
                         ("02", "01", 2, False, first["session"]))
        self.assertNotIn("warm_after", self.state()["ledger"][0])
        self.assertEqual(kept, (report.read_bytes(), [f.read_bytes() for f in evidence]))
        self.assertEqual(sorted(self.run_path("evidence").iterdir()), evidence)
        self.assertEqual(self.state()["tickets"]["01"]["status"], "verified")

    def test_a_launch_without_after_is_unchanged(self):
        self.two()
        first = self.finish()
        self.launch("02")
        w = self.state()["workers"]["02"]
        self.assertNotEqual(w["session"], first["session"])
        self.assertEqual(self.command()[1:3], ["--session-id", w["session"]])
        self.assertEqual(self.command()[-1], fleet.worker_prompt_line(self.run_path("prompts", "02.md"), "02"))
        self.assertNotIn("warm_after", w)
        self.assertNotIn("warm_after", self.state()["ledger"][-1])

    def test_a_chain_counts_each_ticket_and_stops_at_warm_max(self):
        self.cfg["defaults"]["warm_max"] = 3
        self.plan([{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "files": ["third.txt"]}, {"id": "04", "files": ["fourth.txt"]}])
        first = self.finish()
        self.warm("02", "01")
        self.report("02")
        self.stop_with_receipt("02")
        self.verify("02")
        self.warm("03", "02")
        self.assertEqual(self.state()["workers"]["03"]["warm_chain"], 3)
        self.assertEqual(self.state()["workers"]["03"]["session"], first["session"])
        self.report("03")
        self.stop_with_receipt("03")
        self.verify("03")
        out = self.warm("04", "03", expected=1)
        self.assertIn("04 would be ticket 4 in one session, over warm_max 3", out)
        self.assertNotIn("04", self.state()["workers"])
        self.launch("04")                                       # fresh, it starts a chain of its own
        self.assertNotEqual(self.state()["workers"]["04"]["session"], first["session"])

    def test_resume_of_a_warm_ticket_keeps_its_session_and_marks(self):
        self.two()
        first = self.finish()
        self.warm()
        self.receipt("02", returncode=1)
        self.call("status", "demo")
        self.call("launch", "demo", "02", "writer", "--tier", "standard", "--resume")
        w = self.state()["workers"]["02"]
        self.assertEqual((w["session"], w["warm_after"], w["warm_chain"]), (first["session"], "01", 2))
        self.assertTrue(self.command()[-1].startswith("Continue as fleet worker 02"))
        e = self.state()["ledger"][-1]
        self.assertEqual((e["resume"], e["warm_after"]), (True, "01"))

    def test_a_large_earlier_session_warns_but_still_launches(self):
        self.cfg["defaults"]["warm_context_tokens"] = 100000
        self.two()
        first = self.finish()
        log = self.claude_home / "projects" / "-repo" / f"{first['session']}.jsonl"
        log.parent.mkdir(parents=True)
        usage = {"input_tokens": 20, "cache_read_input_tokens": 150000, "cache_creation_input_tokens": 3000,
                 "output_tokens": 500}
        log.write_text(json.dumps({"type": "assistant", "message": {"id": "m1", "usage": usage}}) + "\n")
        out = self.warm()
        self.assertIn("warning: 01's session already holds about 154K tokens of context, above "
                      "warm_context_tokens 100K", out)
        self.assertEqual(self.state()["workers"]["02"]["state"], "running")

    def test_a_small_or_unread_session_launches_without_a_warning(self):
        self.two()
        first = self.finish()
        self.assertNotIn("warning", self.warm())               # no log yet: the size is unknown
        log = self.claude_home / "projects" / "-repo" / f"{first['session']}.jsonl"
        self.assertFalse(log.exists())

    def test_the_reviewer_has_its_own_session_and_only_the_writer_s_is_continued(self):
        self.cfg["providers"]["reviewer"].update(launch=RESUMABLE, session="--session-id {id}", resume="--resume {id}")
        self.two(review="cross-all")
        self.launch()
        (self.repo / "owned.txt").write_text("implemented change\n")
        self.report()
        self.stop_with_receipt()
        self.review_prompt()
        self.launch("review-01", "reviewer", "review")
        self.report("review-01")
        self.stop_with_receipt("review-01")
        self.verify("review-01")
        self.verify()
        workers = self.state()["workers"]
        self.assertNotEqual(workers["review-01"]["session"], workers["01"]["session"])
        out = self.warm(after="review-01", expected=1)
        self.assertIn("02 cannot continue review-01's session (--after review-01): review-01 is a reviewer; only a "
                      "writer's session is continued", out)
        self.warm()
        self.assertEqual(self.state()["workers"]["02"]["session"], workers["01"]["session"])


class Refusals(WarmCase):
    def refused(self, out, *parts):
        for part in parts:
            self.assertIn(part, out)
        self.assertNotIn("02", self.state()["workers"])

    def test_an_unverified_earlier_ticket(self):
        self.two()
        self.launch()
        self.report()
        self.stop_with_receipt()
        self.refused(self.warm(expected=1), "02 cannot continue 01's session (--after 01): 01 is pending, not "
                                            "verified; accept or verify it first")

    def test_an_earlier_worker_whose_exit_is_not_confirmed(self):
        self.cfg["defaults"]["max_parallel"] = self.cfg["providers"]["writer"]["max"] = 2
        self.two()
        self.finish()
        f = self.run_path("run.json")
        state = json.loads(f.read_text())
        state["workers"]["01"]["state"] = "unknown"
        state["workers"]["01"]["exit_file"] = str(self.run_path("exits", "gone.json"))
        fleet.save_run(self.repo, "demo", state)
        self.refused(self.warm(expected=1), "01's worker exit is not confirmed (unknown); stop it or recover it first")

    def test_an_adapter_that_cannot_resume(self):
        self.two({"provider": "reviewer"})
        self.finish()
        self.refused(self.warm(provider="reviewer", expected=1),
                     "reviewer's adapter cannot resume a session (it needs a {session} slot and session/resume "
                     "fields); launch without --after")

    def test_another_model_or_effort(self):
        self.cfg["providers"]["writer"]["tiers"]["light"] = {"model": "writer-mini", "effort": "high"}
        self.two({"tier": "light"})
        self.finish()
        self.refused(self.warm(tier="light", expected=1),
                     "its session ran writer:writer-model@high, and 02 would run writer:writer-mini@high; a session "
                     "continues only on the same provider (account), model and effort")

    def test_another_provider_even_of_the_same_kind(self):
        self.cfg["providers"]["writer-co"] = json.loads(json.dumps(self.cfg["providers"]["writer"]))
        self.cfg["providers"]["writer-co"]["tiers"] = {t: {"model": "writer-model", "effort": "high"}
                                                       for t in ("standard", "heavy", "light", "review")}
        self.two({"provider": "writer-co"})
        self.finish()
        self.refused(self.warm(provider="writer-co", expected=1),
                     "its session ran writer:writer-model@high, and 02 would run writer-co:writer-model@high")

    def test_a_steer_that_moves_the_ticket(self):
        self.cfg["providers"]["writer-co"] = json.loads(json.dumps(self.cfg["providers"]["writer"]))
        self.two()
        self.finish()
        self.call("steer", "provider", "demo", "writer-co")
        self.refused(self.warm(expected=1), "and 02 would run writer-co:writer-model@high")

    def test_a_heavy_ticket(self):
        self.two({"tier": "heavy"})
        self.finish()
        self.refused(self.warm(tier="heavy", expected=1),
                     "02 is a heavy ticket; only light or standard tickets start warm")

    def test_a_worktree_run(self):
        trees = tempfile.TemporaryDirectory()
        self.addCleanup(trees.cleanup)
        self.cfg["defaults"]["worktree_root"] = trees.name
        self.two(isolation="worktree")
        self.refused(self.warm(expected=1), "this is a worktree run. 01's session ran in a tree that accept removed")
        self.assertNotIn("worktrees", self.state())          # refused before any tree was made

    def test_an_investigator_on_either_side(self):
        self.plan([{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "kind": "investigate", "files": []}])
        self.finish()
        out = self.warm("03", expected=1)
        self.assertIn("only an implement ticket continues a writer's session, and 03 is an investigate ticket", out)
        self.call("launch", "demo", "03", "writer", "--tier", "standard")
        self.report("03")
        self.stop_with_receipt("03")
        self.verify("03")
        self.refused(self.warm(after="03", expected=1), "03 is an investigate ticket; only a writer's session is "
                                                        "continued")

    def test_a_session_another_ticket_already_continued(self):
        self.cfg["defaults"]["max_parallel"] = self.cfg["providers"]["writer"]["max"] = 2
        self.plan([{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "files": ["third.txt"]}])
        self.finish()
        self.warm("02")
        out = self.warm("03", expected=1)
        self.assertIn("02 already continued that session; a session takes one ticket at a time, so use --after 02",
                      out)

    def test_a_ticket_relaunched_fresh_after_using_the_session_still_counts(self):
        self.cfg["defaults"]["max_parallel"] = self.cfg["providers"]["writer"]["max"] = 2
        self.plan([{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "files": ["third.txt"]}])
        self.finish()
        self.warm("02")
        self.stop_with_receipt("02")
        self.launch("02")                                  # relaunched fresh: its record now holds a new session
        self.assertNotEqual(self.state()["workers"]["02"]["session"], self.state()["workers"]["01"]["session"])
        out = self.warm("03", expected=1)                  # the ledger still shows 02 used 01's session
        self.assertIn("02 already continued that session", out)

    def test_unknown_self_and_flag_conflicts(self):
        self.two()
        self.finish()
        self.refused(self.warm(after="nope", expected=1), "no ticket 'nope' in this run")
        self.refused(self.warm(after="02", expected=1), "a ticket cannot follow itself")
        self.refused(self.warm("02", "01", "writer", "standard", 1, "--resume"),
                     "--resume continues 02's own session and --after hands it 01's; pass one")
        self.launch("02")                                       # with a session of its own, both flags still refuse
        self.receipt("02", returncode=1)
        self.call("status", "demo")
        out = self.warm("02", "01", "writer", "standard", 1, "--resume")
        self.assertIn("--resume continues 02's own session and --after hands it 01's; pass one", out)
        self.assertIn("drop --after", self.call("launch", "demo", "--ready", "--after", "01", expected=1))


class Plan(WarmCase):
    def test_after_is_saved_as_a_blocker_and_launch_ready_starts_the_ticket_warm(self):
        self.two({"after": "01"}, prompts=False)
        t = self.state()["tickets"]["02"]
        self.assertEqual((t["after"], t["blockers"]), ("01", ["01"]))
        out = self.call("launch", "demo", "--ready")
        self.assertIn("LAUNCHED 01 ", out)
        self.assertIn("SKIPPED 02: blocked by 01", out)
        self.report("01")
        self.stop_with_receipt("01")
        self.verify("01")
        out = self.call("launch", "demo", "--ready")
        self.assertIn("LAUNCHED 02 writer:writer-model@high standard", out)
        w = self.state()["workers"]
        self.assertEqual((w["02"]["session"], w["02"]["warm_after"]), (w["01"]["session"], "01"))
        self.assertEqual(self.command()[1:3], ["--resume", w["01"]["session"]])
        self.assertTrue(self.command()[-1].startswith("Fleet ticket 01 is closed"))

    def test_a_listed_blocker_is_not_doubled_and_a_plan_without_after_is_unchanged(self):
        self.plan([{"id": "01", "files": ["owned.txt"]},
                   {"id": "02", "files": ["next.txt"], "blockers": ["01"], "after": "01"},
                   {"id": "03", "files": ["third.txt"]}])
        tickets = self.state()["tickets"]
        self.assertEqual(tickets["02"]["blockers"], ["01"])
        self.assertNotIn("after", tickets["03"])
        self.assertEqual(tickets["03"]["blockers"], [])

    def test_launch_ready_skips_a_ticket_that_cannot_start_warm_and_says_why(self):
        self.cfg["providers"]["writer-co"] = json.loads(json.dumps(self.cfg["providers"]["writer"]))
        self.two({"after": "01"}, prompts=False)
        self.call("launch", "demo", "--ready")
        self.report("01")
        self.stop_with_receipt("01")
        self.verify("01")
        self.call("steer", "provider", "demo", "writer-co")
        out = self.call("launch", "demo", "--ready")
        self.assertIn("SKIPPED 02: 02 cannot continue 01's session (--after 01): its session ran "
                      "writer:writer-model@high, and 02 would run writer-co:writer-model@high", out)
        self.assertIn("to start it fresh, launch it yourself without --after", out)
        self.assertIn("NONE ready", out)
        self.call("steer", "reset", "demo")
        self.assertIn("LAUNCHED 02", self.call("launch", "demo", "--ready"))

    def test_plan_refuses_an_after_launch_could_never_honour(self):
        self.cfg["providers"]["writer"]["tiers"]["light"] = {"model": "writer-light", "effort": "high"}
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off", *self.off_reason("off"))
        base = {"id": "01", "files": ["owned.txt"]}
        cases = {
            "must name another ticket of this run": [{"id": "02", "files": ["b.txt"], "after": "zz"}],
            "after \"02\" must name another": [{"id": "02", "files": ["b.txt"], "after": "02"}],
            "02 is heavy; only light or standard tickets start warm": [
                {"id": "02", "files": ["b.txt"], "after": "01", "tier": "heavy"}],
            "both tickets must be implement tickets": [{"id": "02", "files": [], "kind": "investigate", "after": "01"}],
            "plans writer:writer-light@high while 01 plans writer:writer-model@high": [
                {"id": "02", "files": ["b.txt"], "after": "01", "tier": "light"}],
            "reviewer's adapter cannot resume a session": [
                {"id": "02", "files": ["b.txt"], "after": "01", "provider": "reviewer"}],
            "03 already follows 01, and a session takes one ticket at a time; chain 02 after 03 instead": [
                {"id": "02", "files": ["b.txt"], "after": "01"}, {"id": "03", "files": ["c.txt"], "after": "01"}],
            "its after chain holds 5 tickets, over warm_max 4": [
                {"id": f"0{n}", "files": [f"{n}.txt"], "after": f"0{n - 1}"} for n in range(2, 6)],
        }
        for message, rest in cases.items():
            with self.subTest(message):
                tickets = [dict(base), *json.loads(json.dumps(rest))]
                if rest[0].get("provider") == "reviewer":
                    tickets[0]["provider"] = "reviewer"
                out = self.call("plan", "demo", "--file", self.plan_file(tickets), expected=1)
                self.assertIn(message, out)
                self.assertEqual(self.state()["tickets"], {})

    def test_plan_refuses_after_in_a_worktree_run(self):
        trees = tempfile.TemporaryDirectory()
        self.addCleanup(trees.cleanup)
        self.cfg["defaults"]["worktree_root"] = trees.name
        self.call("init", "demo", "--workspace", "workspace:1", "--review", "off", *self.off_reason("off"),
                  "--isolation", "worktree")
        out = self.call("plan", "demo", "--file", self.plan_file(
            [{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"], "after": "01"}]), expected=1)
        self.assertIn("02: after \"01\": a worktree run cannot keep a worker warm", out)

    def test_add_may_follow_a_saved_ticket_and_counts_its_chain(self):
        self.cfg["defaults"]["warm_max"] = 2
        self.two({"after": "01"})
        self.finish()
        out = self.call("add", "demo", "--file", self.plan_file([{"id": "03", "files": ["c.txt"], "after": "02"}]),
                        expected=1)
        self.assertIn("03: its after chain holds 3 tickets, over warm_max 2", out)
        out = self.call("add", "demo", "--file", self.plan_file([{"id": "03", "files": ["c.txt"], "after": "01"}]),
                        expected=1)
        self.assertIn("02 already follows 01", out)
        self.call("add", "demo", "--file", self.plan_file([{"id": "03", "files": ["c.txt"]},
                                                           {"id": "04", "files": ["d.txt"], "after": "03"}]))
        self.assertEqual(self.state()["tickets"]["04"]["blockers"], ["03"])


class Dispatch(WarmCase):
    def test_supervise_dispatch_starts_the_planned_ticket_warm(self):
        self.cfg["defaults"]["max_parallel"] = 2
        self.two({"after": "01"}, prompts=False)
        self.call("launch", "demo", "--ready")
        self.report("01")
        self.stop_with_receipt("01")
        self.verify("01")
        out = self.call("supervise", "demo", "--timeout", "1", "--interval", "1", "--settle", "0",
                        "--auto", "dispatch", expected=2)
        self.assertRegex(out, r"\nLAUNCHED 02 writer:writer-model@high standard surface:\d+\n")
        w = self.state()["workers"]
        self.assertEqual((w["02"]["session"], w["02"]["warm_after"]), (w["01"]["session"], "01"))
        self.assertEqual(self.command()[1:3], ["--resume", w["01"]["session"]])


class Cost(WarmCase):
    def test_cost_shows_the_chain_and_json_lists_it(self):
        self.plan([{"id": "01", "files": ["owned.txt"]}, {"id": "02", "files": ["next.txt"]},
                   {"id": "03", "files": ["third.txt"]}])
        first = self.finish()
        self.warm("02")
        self.report("02")
        self.stop_with_receipt("02")
        self.verify("02")
        self.warm("03", "02")
        out = self.call("cost", "demo")
        self.assertIn("  chain 01 -> 02 -> 03: one writer session, 3 tickets, 3 launches\n", out)
        data, _ = json.JSONDecoder().raw_decode(self.call("cost", "demo", "--json"))
        self.assertEqual(data["chains"], [{"session": first["session"], "provider": "writer",
                                           "tickets": ["01", "02", "03"], "launches": 3}])
        self.assertEqual([e.get("warm_chain") for e in data["ledger"]], [None, 2, 3])

    def test_cost_without_a_warm_launch_lists_no_chain(self):
        self.two()
        self.finish()
        self.launch("02")
        self.assertNotIn("chain", self.call("cost", "demo"))

    def test_each_ticket_in_the_session_counts_only_its_own_calls(self):
        self.two()
        self.launch()
        f = self.run_path("run.json")
        state = json.loads(f.read_text())
        state["ledger"][0]["start"] -= 3600                   # the first ticket ran an hour ago
        fleet.save_run(self.repo, "demo", state)
        start, session = state["ledger"][0]["start"], state["workers"]["01"]["session"]
        log = self.claude_home / "projects" / "-repo" / f"{session}.jsonl"
        log.parent.mkdir(parents=True)

        def call(mid, at, n):
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(at))
            return json.dumps({"type": "assistant", "timestamp": stamp,
                               "message": {"id": mid, "usage": {"input_tokens": n, "output_tokens": 1}}}) + "\n"

        def exit_at(ticket, when):
            self.receipt(ticket)
            os.utime(self.state()["workers"][ticket]["exit_file"], (when, when))
            self.call("status", "demo")
        log.write_text(call("m1", start + 5, 100))
        self.report()
        exit_at("01", start + 20)
        self.verify()
        self.warm()
        launched = self.state()["workers"]["02"]["launched_at"]
        with log.open("a") as fh:
            fh.write(call("m2", launched + 5, 7))
        exit_at("02", launched + 20)
        self.call("cost", "demo")
        first, warm = self.state()["ledger"]
        self.assertEqual((first["tokens"]["input"], warm["tokens"]["input"]), (100, 7))


class Config(WarmCase):
    def test_warm_keys_are_positive_whole_numbers(self):
        self.assertEqual((fleet.DEFAULTS["warm_max"], fleet.DEFAULTS["warm_context_tokens"]), (4, 150000))
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("warm_max = 0", "warm_max = true", 'warm_context_tokens = "lots"', "warm_context_tokens = -1"):
                path = Path(tmp) / "config.toml"
                path.write_text(f"[defaults]\n{bad}\n")
                with self.subTest(bad), self.assertRaisesRegex(fleet.FleetError, "must be a positive whole number"):
                    REAL_LOAD_CONFIG(path)
            path.write_text("[defaults]\nwarm_max = 2\nwarm_context_tokens = 90000\n")
            self.assertEqual(REAL_LOAD_CONFIG(path)["defaults"]["warm_max"], 2)

    def test_a_run_keeps_the_warm_max_it_was_started_with(self):
        self.cfg["defaults"]["warm_max"] = 1
        self.two()
        self.cfg["defaults"]["warm_max"] = 4                 # raised after init: this run keeps 1
        self.finish()
        self.assertIn("over warm_max 1", self.warm(expected=1))


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class WarmAdapterSmoke(FleetCase):
    """A planned `after` through launch --ready on each shipped adapter that can resume, in headless tmux, against
    the strict fake CLI: it accepts the warm worker only with --resume of the earlier session and the exact line."""

    def setUp(self):
        super().setUp()
        self.bindir = self.root / "bin"
        install_fakes(self.bindir)
        self.log = self.root / "fake-log"
        self.log.mkdir()
        self.expect = self.root / "expect.json"
        self.env.update(PATH=f"{self.bindir}:{self.env['PATH']}", FAKE_CLI_LOG=str(self.log),
                        FAKE_CLI_EXPECT=str(self.expect), **LEAD_ACCOUNTS)
        self.evidence = self.root / "evidence.txt"
        self.evidence.write_text("Read the fake worker's report; it changed no files.\n")

    def expectations(self, name, **extra):
        p = SHIPPED[name]
        model, effort = fleet.resolve_model(p, "standard", None, None)
        want = {"model": model, "effort": effort, "session": "new",
                "account_dir": (str(Path(p["env"][p["account_env"]].replace("~", str(self.home), 1)))
                                if p.get("account_env") in p.get("env", {}) else None), **extra}
        self.expect.write_text(json.dumps(want))

    def calls(self, name):
        f = self.log / f"{SHIPPED[name]['bin']}.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []

    def errors(self, name):
        f = self.log / f"{SHIPPED[name]['bin']}.errors"
        return f.read_text() if f.exists() else ""

    def workers(self):
        return json.loads((self.repo / ".fleet/runs/smoke/run.json").read_text())["workers"]

    def run_path(self, *parts):
        return self.repo.joinpath(".fleet/runs/smoke", *parts)

    def start(self, name):
        self.write_config(f'review = "off"\nprefer = ["{name}"]\n[providers.{name}]\nenabled = true\n')
        self.fleet("init", "smoke")
        (self.repo / "plan.json").write_text(json.dumps({"tickets": [
            {"id": i, "task": f"Change {f}.", "files": [f], "provider": name, "tier": "standard",
             "context": "A smoke test.", "checks": {"manual": "manual prose review is sufficient"}, **extra}
            for i, f, extra in (("01", "a.txt", {}), ("02", "b.txt", {"after": "01"}))]}))
        self.fleet("plan", "smoke", "--file", str(self.repo / "plan.json"))
        self.expectations(name)
        self.assertRegex(self.fleet("launch", "smoke", "--ready"), r"LAUNCHED 01 .*\n(.|\n)*SKIPPED 02: blocked by 01")
        self.hand_back(name, "01")
        self.fleet("verify", "smoke", "01", "--evidence", str(self.evidence))
        return self.workers()["01"]["session"]

    def hand_back(self, name, i):
        self.assertIn("needs-verification", self.wait_for("smoke", rf"^REPORT {i}\b"))
        self.assertEqual(self.errors(name), "")
        self.assertRegex(self.fleet("stop", "smoke", i, "--timeout", "10"), rf"{i}: exited \(@\d+\)")

    def warm_resumes(self, name):
        session = self.start(name)
        self.expectations(name, session="resume", session_id=session, warm_after="01")
        self.assertIn("LAUNCHED 02 ", self.fleet("launch", "smoke", "--ready"))
        self.hand_back(name, "02")
        first, warm = self.calls(name)
        self.assertEqual((first["ticket"], first["warm_after"]), ("01", None))
        self.assertEqual((warm["ticket"], warm["warm_after"], warm["resume"]), ("02", "01", False))
        self.assertEqual(warm["argv"][:2], ["--resume", session])
        self.assertEqual(warm["argv"][-1], fleet.warm_prompt_line(self.run_path("prompts", "02.md"), "02", "01",
                                                                  self.run_path("reports", "02.md"),
                                                                  self.run_path("inbox", "02.md")))
        scope = json.loads(self.run_path("settings", "02.scope.json").read_text())
        self.assertEqual(scope["files"], ["b.txt"])
        self.assertIn("(warm, after 01)", self.run_path("reports", "02.md").read_text())
        self.assertIn("01 -> 02: one", self.fleet("cost", "smoke"))
        return warm

    def test_claude(self):
        warm = self.warm_resumes("claude")
        self.assertIn("--settings", warm["argv"])

    def test_claude_co(self):
        self.warm_resumes("claude-co")

    def test_a_lost_session_exits_as_usual_and_a_fresh_launch_follows(self):
        session = self.start("claude")
        self.expectations("claude", session="resume", session_id=session, warm_after="01", unknown_sessions=[session])
        self.fleet("prompt", "smoke", "02")
        self.fleet("launch", "smoke", "02", "claude", "--tier", "standard", "--after", "01", "--settle", "0")
        self.exited("02")                                       # reported as any exit is
        self.assertIn("No conversation found with session ID: " + session, self.fleet("peek", "smoke", "02"))
        self.expectations("claude")
        self.fleet("launch", "smoke", "02", "claude", "--tier", "standard", "--settle", "0")
        self.hand_back("claude", "02")
        self.assertNotEqual(self.workers()["02"]["session"], session)
        self.assertEqual(self.calls("claude")[-1]["warm_after"], None)

    def exited(self, i):
        deadline, seen = time.monotonic() + 20, []
        while time.monotonic() < deadline:
            out = self.fleet("wait", "smoke", "--timeout", "5", "--interval", "1", "--settle", "0", "--stall", "0",
                             expected=None)
            seen.append(out)
            if re.search(rf"^EXITED {i}\b", out, re.M):
                return out
        self.fail("no EXITED line:\n" + "".join(seen) + self.diagnostics())


if __name__ == "__main__":
    unittest.main()
