"""Worktree isolation (#15, TEAM-REVIEW P16): `fleet init --isolation worktree` gives each implement ticket its own
Git worktree outside the checkout, made from the owner's checkout with its uncommitted edits, and `fleet merge`
(which accept runs) applies the ticket's diff back with plain `git apply`. Every test uses real temporary Git
repositories and checks that the owner's HEAD, index and refs never change. No live agent, cmux or network is used;
the last test drives scripted workers through headless tmux."""
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from test_runtime import RuntimeCase, fleet  # noqa: E402
from fleet_harness import FIXTURES, HAS_TMUX, FleetCase  # noqa: E402

HOOKED_LAUNCH = "{bin} --model {model} --effort {effort} {profile} {settings} {prompt}"


class WorktreeCase(RuntimeCase):
    """RuntimeCase with room for parallel workers and a private folder for the run's worktrees."""

    def setUp(self):
        super().setUp()
        trees = tempfile.TemporaryDirectory()
        self.addCleanup(trees.cleanup)
        self.trees_root = Path(trees.name).resolve()
        self.cfg["defaults"].update(max_parallel=4, worktree_root=str(self.trees_root))
        for p in self.cfg["providers"].values():
            p["max"] = 3
        self.addCleanup(self.prune)

    def prune(self):
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo, capture_output=True)

    def start(self, tickets, review="off", isolation="worktree"):
        args = ["init", "demo", "--workspace", "workspace:1", "--review", review, *self.off_reason(review)]
        self.call(*args, *(["--isolation", isolation] if isolation else []))
        for ticket in tickets:
            path = self.repo / ".scratch" / (ticket["id"] + ".md")
            path.parent.mkdir(exist_ok=True)
            path.write_text("# Ticket " + ticket["id"] + "\nImplement the change.\n")
            ticket.setdefault("provider", "writer")
            ticket.setdefault("tier", "standard")
            ticket["ticket"] = str(path)
        plan = self.repo / ".scratch" / "plan.json"
        plan.write_text(json.dumps({"tickets": tickets}))
        self.call("plan", "demo", "--file", str(plan))
        for ticket in tickets:
            self.prompt(ticket["id"])

    def tree(self, i):
        return Path(self.state()["worktrees"][i]["path"])

    def work(self, i, files, report=True):
        """What a worker does in its own tree: write files (None deletes one) and hand back."""
        for name, text in files.items():
            path = self.tree(i) / name
            if text is None:
                path.unlink()
            elif isinstance(text, bytes):
                path.write_bytes(text)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        if report:
            self.report(i)

    def owner_git(self):
        """Everything Fleet must never change: HEAD, the index file's bytes, every ref and the stash."""
        index = Path(self.git("rev-parse", "--git-path", "index").strip())
        return {"head": self.git("rev-parse", "HEAD"),
                "index": hashlib.sha256((self.repo / index).read_bytes()).hexdigest(),
                "refs": self.git("for-each-ref"), "stash": self.git("stash", "list")}

    def accept(self, i, expected=0):
        return self.call("accept", "demo", i, "--evidence", str(self.evidence), expected=expected)

    def cwd_of_launch(self, n=-1):
        """The directory cmux was told to start the n-th launched worker in."""
        calls = [c for c in self.cmux_calls if c[0] in ("new-split", "new-surface")]
        c = calls[n]
        if c[0] == "new-surface":
            return c[c.index("--working-directory") + 1]
        return shlex.split(c[c.index("--command") + 1])[1]

    def registered(self):
        out = self.git("worktree", "list", "--porcelain")
        return [Path(line[9:]).resolve() for line in out.splitlines() if line.startswith("worktree ")][1:]


class Isolation(WorktreeCase):
    def setUp(self):
        super().setUp()
        # Written before the quality bar: these tests cover verify and accept's mechanics with header-less
        # reports, so their runs start under acceptance = "warn". tests/test_quality_bar.py covers "strict".
        self.cfg["defaults"]["acceptance"] = "warn"

    def test_parallel_tickets_get_the_owners_edits_and_accept_applies_both_without_touching_git_state(self):
        (self.repo / ".gitignore").write_text(".env\nbuild/\n")
        (self.repo / "tracked.txt").write_text("committed\n")
        (self.repo / "staged.txt").write_text("committed\n")
        self.git("add", ".gitignore", "tracked.txt", "staged.txt")
        self.git("commit", "-qm", "files")
        (self.repo / "tracked.txt").write_text("owner's unstaged edit\n")        # tracked, not staged
        (self.repo / "staged.txt").write_text("owner's staged edit\n")
        self.git("add", "staged.txt")                                             # staged
        (self.repo / "fresh.txt").write_text("owner's untracked file\n")         # untracked, not ignored
        (self.repo / ".env").write_text("SECRET=1\n")                             # ignored: never copied
        before = self.owner_git()
        self.start([{"id": "a", "files": ["a.txt"]}, {"id": "b", "files": ["b.txt"]}])
        self.launch("a")
        self.launch("b")
        self.assertEqual(Path(self.cwd_of_launch(0)), self.tree("a"))
        self.assertEqual(Path(self.cwd_of_launch(1)), self.tree("b"))
        for i in ("a", "b"):
            tree = self.tree(i)
            self.assertTrue(tree.is_relative_to(self.trees_root))
            self.assertFalse(tree.is_relative_to(self.repo))
            self.assertEqual((tree / "tracked.txt").read_text(), "owner's unstaged edit\n")
            self.assertEqual((tree / "staged.txt").read_text(), "owner's staged edit\n")
            self.assertEqual((tree / "fresh.txt").read_text(), "owner's untracked file\n")
            self.assertFalse((tree / ".env").exists())
        self.assertNotEqual(self.tree("a"), self.tree("b"))
        self.work("a", {"a.txt": "from a\n", "build/out.bin": "ignored build output\n"})
        self.work("b", {"b.txt": "from b\n"})
        self.assertFalse((self.repo / "a.txt").exists())                        # nothing reached the checkout yet
        for i in ("a", "b"):
            self.stop_with_receipt(i)
        out = self.accept("a")
        self.assertIn("MERGED a: applied 1 file(s) to the checkout's working tree (a.txt); the index is untouched", out)
        self.assertIn("a: verified", out)
        self.accept("b")
        self.assertEqual((self.repo / "a.txt").read_text(), "from a\n")
        self.assertEqual((self.repo / "b.txt").read_text(), "from b\n")
        self.assertFalse((self.repo / "build").exists())                         # only the ticket's files merge
        self.assertEqual(self.owner_git(), before)                               # HEAD, index bytes, refs, stash
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "staged.txt\n")  # still staged
        self.assertEqual((self.repo / "tracked.txt").read_text(), "owner's unstaged edit\n")
        self.assertEqual(self.registered(), [])                                  # accept removed both trees
        self.assertEqual(self.state()["worktrees"], {})
        out = self.call("check", "demo")
        self.assertIn("OK: captured HEAD and index states match the run baseline", out)
        self.assertIn("WORKTREES none", out)

    def test_overlapping_tickets_run_in_parallel_and_the_second_accept_meets_conflict(self):
        (self.repo / "shared.txt").write_text("one\ntwo\nthree\n")
        self.git("add", "shared.txt")
        self.git("commit", "-qm", "shared")
        self.start([{"id": "a", "files": ["shared.txt", "a.txt"]}, {"id": "b", "files": ["shared.txt", "b.txt"]}])
        self.launch("a")
        self.launch("b")                                       # a shared run refuses this overlap
        self.work("a", {"shared.txt": "ONE\ntwo\nthree\n", "a.txt": "a\n"})
        self.work("b", {"shared.txt": "one\ntwo\nTHREE\n", "b.txt": "b\n"})
        self.stop_with_receipt("a")
        self.stop_with_receipt("b")
        before = self.owner_git()
        self.accept("a")
        out = self.accept("b", expected=1)
        self.assertIn("CONFLICT b: shared.txt", out)
        self.assertIn("changed in the checkout since the worktree was made", out)
        self.assertIn("nothing was applied or verified", out)
        self.assertIn("git merge-file", out)
        self.assertEqual((self.repo / "shared.txt").read_text(), "ONE\ntwo\nthree\n")  # a's, untouched by b
        self.assertFalse((self.repo / "b.txt").exists())                              # nothing half-applied
        self.assertEqual(self.state()["tickets"]["b"]["status"], "pending")
        self.assertTrue(self.tree("b").is_dir())
        base = self.repo / ".fleet/runs/demo/merge/b/shared.txt.base"
        self.assertEqual(base.read_text(), "one\ntwo\nthree\n")
        # The lead merges by hand, as the refusal shows, and records it; accept then goes through.
        subprocess.run(["git", "merge-file", str(self.repo / "shared.txt"), str(base), str(self.tree("b") / "shared.txt")],
                       check=True)
        (self.repo / "b.txt").write_text("b\n")
        self.call("merge", "demo", "b", "--resolved", "merged shared.txt by hand with git merge-file")
        self.accept("b")
        self.assertEqual((self.repo / "shared.txt").read_text(), "ONE\ntwo\nTHREE\n")
        self.assertIn("Merged b by hand", "\n".join(self.state()["decisions"]))
        self.assertEqual(self.owner_git(), before)

    def test_merge_handles_new_deleted_binary_and_mode_changed_files(self):
        (self.repo / "gone.txt").write_text("delete me\n")
        (self.repo / "blob.bin").write_bytes(bytes(range(256)))
        (self.repo / "run.sh").write_text("#!/bin/sh\necho hi\n")
        self.git("add", "gone.txt", "blob.bin", "run.sh")
        self.git("commit", "-qm", "files")
        before = self.owner_git()
        files = ["new.txt", "gone.txt", "blob.bin", "run.sh", "sub/deep.txt"]
        self.start([{"id": "a", "files": files}])
        self.launch("a")
        self.work("a", {"new.txt": "new\n", "gone.txt": None, "blob.bin": bytes(reversed(range(256))) + b"\0x",
                        "sub/deep.txt": "deep\n"})
        (self.tree("a") / "run.sh").chmod(0o755)
        self.stop_with_receipt("a")
        out = self.call("merge", "demo", "a")
        self.assertIn("MERGED a: applied 5 file(s)", out)
        self.assertEqual((self.repo / "new.txt").read_text(), "new\n")
        self.assertEqual((self.repo / "sub/deep.txt").read_text(), "deep\n")
        self.assertFalse((self.repo / "gone.txt").exists())
        self.assertEqual((self.repo / "blob.bin").read_bytes(), bytes(reversed(range(256))) + b"\0x")
        self.assertTrue(os.access(self.repo / "run.sh", os.X_OK))
        self.assertIn("already in the checkout", self.call("merge", "demo", "a"))  # a second merge changes nothing
        self.accept("a")
        self.assertEqual(self.owner_git(), before)

    def test_a_ticket_repaired_after_its_merge_merges_again_without_conflict(self):
        self.start([{"id": "a", "files": ["a.txt", "b.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "first\n"})
        self.stop_with_receipt("a")
        self.assertIn("MERGED a: applied 1 file(s)", self.call("merge", "demo", "a"))
        self.launch("a")                                         # a repair round in the same tree
        self.work("a", {"a.txt": "first\nsecond\n", "b.txt": "b\n"})
        self.stop_with_receipt("a")
        out = self.call("merge", "demo", "a")
        self.assertIn("MERGED a: applied 2 file(s)", out)
        self.assertIn("only what changed since its last merge", out)
        self.assertEqual((self.repo / "a.txt").read_text(), "first\nsecond\n")
        self.assertEqual((self.repo / "b.txt").read_text(), "b\n")
        self.assertIn("already in the checkout", self.call("merge", "demo", "a"))
        self.accept("a")

    def test_a_repaired_ticket_still_conflicts_when_the_owner_edited_its_merged_file(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "first\n"})
        self.stop_with_receipt("a")
        self.call("merge", "demo", "a")
        (self.repo / "a.txt").write_text("the owner's own change\n")
        self.launch("a")
        self.work("a", {"a.txt": "first\nsecond\n"})
        self.stop_with_receipt("a")
        out = self.call("merge", "demo", "a", expected=1)
        self.assertIn("CONFLICT a: a.txt", out)
        self.assertIn("changed in the checkout since Fleet last merged this ticket", out)
        self.assertEqual((self.repo / "a.txt").read_text(), "the owner's own change\n")

    def test_a_repair_after_the_owner_put_a_merged_file_back_conflicts_instead_of_recording_it(self):
        """Final review, 3: a merged file the repair leaves alone must still hold what the merge recorded."""
        (self.repo / "a.txt").write_text("a base\n")
        (self.repo / "b.txt").write_text("b base\n")
        self.git("add", "a.txt", "b.txt")
        self.git("commit", "-qm", "files")
        self.start([{"id": "t", "files": ["a.txt", "b.txt"]}])
        self.launch("t")
        self.work("t", {"a.txt": "a by t\n", "b.txt": "b by t\n"})
        self.stop_with_receipt("t")
        self.call("merge", "demo", "t")
        self.git("checkout", "--", "a.txt")  # the owner puts a.txt back
        self.launch("t")                     # a repair round that touches b.txt only
        self.work("t", {"b.txt": "b by t, repaired\n"})
        self.stop_with_receipt("t")
        out = self.accept("t", expected=1)
        self.assertIn("CONFLICT t: a.txt", out)
        self.assertIn("a.txt: changed in the checkout since Fleet last merged this ticket", out)
        self.assertIn("nothing was applied or verified", out)
        self.assertEqual((self.repo / "a.txt").read_text(), "a base\n")
        self.assertEqual((self.repo / "b.txt").read_text(), "b by t\n")  # nothing half-applied
        self.assertEqual(self.state()["tickets"]["t"]["status"], "pending")
        # Its base copy is the tree's as it was made, so the git merge-file line brings t's change back.
        self.assertEqual((self.repo / ".fleet/runs/demo/merge/t/a.txt.base").read_text(), "a base\n")
        for line in out.splitlines():
            if line.strip().startswith("git merge-file"):
                subprocess.run(shlex.split(line.strip()), cwd=self.repo, check=True)
        self.call("merge", "demo", "t", "--resolved", "ran the git merge-file lines")
        self.accept("t")
        self.assertEqual(((self.repo / "a.txt").read_text(), (self.repo / "b.txt").read_text()),
                         ("a by t\n", "b by t, repaired\n"))

    def test_a_merged_file_that_differs_from_the_tree_after_git_apply_is_refused_not_recorded(self):
        """Final review, 3: once applied, a file whose copy differs from the tree (a filter, line endings) is a
        refusal, and no later merge takes it as merged."""
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "from a\n"})
        self.stop_with_receipt("a")
        real = fleet.tree_git

        def filtering(cwd, *args, **kwargs):
            res = real(cwd, *args, **kwargs)
            if args[:1] == ("apply",) and "--check" not in args:
                (self.repo / "a.txt").write_text("from a\r\n")  # what a line-ending filter could leave
            return res
        with patch.object(fleet, "tree_git", side_effect=filtering):
            out = self.accept("a", expected=1)
        self.assertIn("a: after git apply, a.txt in the checkout differ from a's worktree", out)
        self.assertIn("Its diff is in the checkout's working tree now; nothing was verified", out)
        self.assertNotIn("nothing was applied", out)
        self.assertEqual(self.state()["tickets"]["a"]["status"], "pending")
        self.assertEqual(self.state()["tickets"]["a"]["merged"]["off"], ["a.txt"])  # the run says it reached the checkout
        out = self.accept("a", expected=1)
        self.assertIn("CONFLICT a: a.txt", out)
        self.assertIn("differs from its worktree's copy after git apply", out)
        self.call("merge", "demo", "a", "--resolved", "the owner checked the line endings")
        self.assertIn("a: verified", self.accept("a"))

    def test_merge_resolved_needs_a_reported_conflict(self):
        """Final review, 4: with no CONFLICT reported, there is no merge by hand to record."""
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "a's reviewed work\n"})
        self.stop_with_receipt("a")
        out = self.call("merge", "demo", "a", "--resolved", "merged it by hand", expected=1)
        self.assertIn("a: fleet merge reported no CONFLICT for a's diff as it is now, so there is no merge by hand to "
                      "record", out)
        self.assertNotIn("merged", self.state()["tickets"]["a"])
        self.assertFalse((self.repo / "a.txt").exists())
        self.accept("a")
        self.assertEqual((self.repo / "a.txt").read_text(), "a's reviewed work\n")

    def test_merge_resolved_refuses_while_a_file_of_the_diff_holds_its_content_from_before_the_merge(self):
        """Final review, 4: the CONFLICT lists every file the diff touches, and --resolved records none of them while
        one still lacks the ticket's change; the tree's change is saved as a patch."""
        (self.repo / "a.txt").write_text("".join(f"line{n}\n" for n in range(1, 11)))
        (self.repo / "b.txt").write_text("b base\n")
        self.git("add", "a.txt", "b.txt")
        self.git("commit", "-qm", "files")
        self.start([{"id": "t", "files": ["a.txt", "b.txt"]}])
        self.launch("t")
        self.work("t", {"a.txt": "".join(f"line{n}\n" for n in range(1, 10)) + "line10 by t\n", "b.txt": "b by t\n"})
        self.stop_with_receipt("t")
        (self.repo / "a.txt").write_text("line1 by owner\n" + "".join(f"line{n}\n" for n in range(2, 11)))
        out = self.accept("t", expected=1)
        self.assertIn("CONFLICT t: a.txt", out)
        self.assertIn("b.txt: applies cleanly; merge it with the rest", out)
        lines = [x.strip() for x in out.splitlines() if x.strip().startswith("git merge-file")]
        self.assertEqual(len(lines), 2)
        subprocess.run(shlex.split(lines[0]), cwd=self.repo, check=True)  # a.txt's line only
        out = self.call("merge", "demo", "t", "--resolved", "merged a.txt by hand", expected=1)
        self.assertIn("t: b.txt in the checkout still hold their content from before this merge", out)
        self.assertNotIn("merged", self.state()["tickets"]["t"])
        subprocess.run(shlex.split(lines[1]), cwd=self.repo, check=True)
        out = self.call("merge", "demo", "t", "--resolved", "ran both git merge-file lines")
        self.assertIn("MERGED t by hand: recorded the checkout's a.txt, b.txt as its merge", out)
        patch_file = self.repo / ".fleet/runs/demo/worktrees/t.patch"
        self.assertIn(f"its worktree's changes are saved in {patch_file}", out)
        self.assertIn("+line10 by t", patch_file.read_text())
        self.accept("t")
        self.assertEqual((self.repo / "b.txt").read_text(), "b by t\n")
        self.assertEqual((self.repo / "a.txt").read_text(),
                         "line1 by owner\n" + "".join(f"line{n}\n" for n in range(2, 10)) + "line10 by t\n")

    def test_accept_says_the_diff_is_applied_when_verify_then_refuses(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "a\n"})
        self.stop_with_receipt("a")
        with mock.patch.object(fleet, "cmd_verify", side_effect=fleet.FleetError("forced refusal")):
            out = self.accept("a", expected=1)
        self.assertIn("MERGED a: applied 1 file(s)", out)
        self.assertIn("forced refusal; nothing was verified; its diff is already in the checkout's working tree", out)
        self.assertIn("already in the checkout", self.accept("a"))   # not applied twice
        self.assertEqual((self.repo / "a.txt").read_text(), "a\n")

    def test_drop_after_a_merge_says_the_merged_changes_stay(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "a\n"})
        self.stop_with_receipt("a")
        self.call("merge", "demo", "a")
        out = self.call("drop", "demo", "a", "--reason", "owner changed course")
        self.assertIn("its merged changes to a.txt stay in the checkout's working tree", out)
        self.assertNotIn("nothing it did reached the checkout", out)

    def test_a_failed_launch_removes_the_tree_it_made(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        with mock.patch.object(fleet, "build_launch", side_effect=fleet.FleetError("boom")):
            self.assertIn("boom", self.call("launch", "demo", "a", "writer", "--tier", "standard", expected=1))
        self.assertNotIn("a", self.state().get("worktrees", {}))
        self.assertEqual(self.registered(), [])
        self.assertFalse((self.repo / ".fleet/runs/demo/snapshots/a.json").exists())
        self.launch("a")                                         # the next launch gets a fresh tree
        self.assertTrue(self.tree("a").is_dir())

    def test_absolute_paths_in_a_worktree_header_count_from_the_tree(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        tree = self.tree("a")
        t = self.state()["tickets"]["a"]
        self.assertEqual(fleet.header_file_problems(self.repo, t, False, [str(tree / "a.txt")], tree), [])
        self.assertEqual(fleet.header_file_problems(self.repo, t, False, [str(tree / "b.txt")], tree),
                         ['"b.txt" outside scope'])  # the header's path is quoted: the worker's text

    def test_verify_refuses_an_unmerged_worktree_ticket(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.work("a", {"a.txt": "a\n"})
        self.stop_with_receipt("a")
        self.assertIn("fleet merge demo a", self.verify("a", expected=1))
        self.call("merge", "demo", "a")
        self.verify("a")
        self.assertEqual((self.repo / "a.txt").read_text(), "a\n")

    def test_merge_refuses_in_a_shared_run_and_while_the_worker_runs(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.assertIn("may still be running", self.call("merge", "demo", "a", expected=1))

    def test_shared_runs_are_unchanged(self):
        self.start([{"id": "a", "files": ["a.txt"]}], isolation=None)
        self.assertNotIn("isolation", self.state()["options"])
        self.assertNotIn("worktrees", self.state())
        self.launch("a")
        self.assertEqual(Path(self.cwd_of_launch()), self.repo)
        prompt = (self.repo / ".fleet/runs/demo/prompts/a.md").read_text()
        self.assertIn(f"in the shared checkout at `{self.repo}`", prompt)
        self.assertIn("Do not run build, dev, start or preview servers", prompt)
        self.assertIn("for runs started with --isolation worktree", self.call("merge", "demo", "a", expected=1))
        self.assertNotIn("WORKTREE", self.call("check", "demo"))
        self.assertEqual(self.registered(), [])

    def test_add_allows_overlap_only_in_a_worktree_run(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        more = self.repo / ".scratch/more.json"
        more.write_text(json.dumps({"tickets": [{"id": "b", "task": "Also a.", "files": ["a.txt"], "provider": "writer",
                                                 "checks": [{"manual": "read it"}], "context": "c"}]}))
        self.call("add", "demo", "--file", str(more))
        out = self.call("launch", "demo", "--ready")
        self.assertIn("LAUNCHED a", out)
        self.assertIn("LAUNCHED b", out)                       # side by side, each in its own tree

    def test_add_allows_overlap_within_one_call_in_a_worktree_run(self):
        self.start([{"id": "z", "files": ["z.txt"]}])
        more = self.repo / ".scratch/more.json"
        more.write_text(json.dumps({"tickets": [
            {"id": i, "task": "Change a.", "files": ["a.txt"], "provider": "writer", "checks": [{"manual": "read it"}],
             "context": "c"} for i in ("a", "b")]}))
        self.assertIn("added 2 tickets: a, b", self.call("add", "demo", "--file", str(more)))

    def test_a_shared_run_still_refuses_overlap_within_one_add(self):
        self.start([{"id": "z", "files": ["z.txt"]}], isolation=None)
        more = self.repo / ".scratch/more.json"
        more.write_text(json.dumps({"tickets": [
            {"id": i, "task": "Change a.", "files": ["a.txt"], "provider": "writer", "checks": [{"manual": "read it"}],
             "context": "c"} for i in ("a", "b")]}))
        self.assertIn("b: files overlap unverified ticket a", self.call("add", "demo", "--file", str(more), expected=1))


class Reads(WorktreeCase):
    def setUp(self):
        super().setUp()
        # Written before the quality bar: these tests cover verify and accept's mechanics with header-less
        # reports, so their runs start under acceptance = "warn". tests/test_quality_bar.py covers "strict".
        self.cfg["defaults"]["acceptance"] = "warn"

    def test_diff_and_scope_check_read_the_worktree_against_its_base(self):
        self.start([{"id": "a", "files": ["owned.txt"]}])
        self.launch("a")
        (self.repo / "owner-notes.txt").write_text("the owner works on in the checkout\n")
        self.work("a", {"owned.txt": "worker edit\n", "stray.txt": "outside the ticket\n"})
        self.stop_with_receipt("a")
        out = self.call("diff", "demo", "a", expected=1)
        self.assertIn("stray.txt (created)", out)                # the worker's stray file, in its tree
        self.assertNotIn("owner-notes.txt", out)                 # the owner's checkout is not this worker's
        (self.tree("a") / "stray.txt").unlink()
        diff = self.call("diff", "demo", "a")
        self.assertIn("-owner baseline\n+worker edit\n", diff)
        self.assertEqual((self.repo / "owned.txt").read_text(), "owner baseline\n")

    def test_the_gate_runs_in_the_worktree(self):
        self.start([{"id": "a", "files": ["a.txt"], "context": "c",
                     "checks": [{"run": "test -f a.txt"}, {"run": "pwd; false"}]}])
        self.launch("a")
        self.work("a", {"a.txt": "a\n"})
        out = self.call("gate", "demo", "a", expected=1)
        self.assertIn("GATE a 1/2 passed, scope ok", out)
        evidence = (self.repo / ".fleet/runs/demo/evidence/a.gate.md").read_text()
        self.assertIn(str(self.tree("a")), evidence)
        (self.tree("a") / "cache.tmp").write_text("a check's cache\n")   # written by a check, worker stopped
        self.stop_with_receipt("a")
        self.start_gate_check_writes()

    def start_gate_check_writes(self):
        """A file the gate's own commands write in the tree while its worker is stopped is not blamed on it."""
        state = self.state()
        state["tickets"]["a"]["commands"] = [{"run": "echo out > made-by-check.txt", "cwd": ".", "timeout": 30}]
        fleet.save_run(self.repo, "demo", state)
        (self.tree("a") / "cache.tmp").unlink()
        self.assertIn("GATE a 1/1 passed, scope ok", self.call("gate", "demo", "a"))
        self.assertTrue((self.tree("a") / "made-by-check.txt").exists())
        self.assertFalse((self.repo / "made-by-check.txt").exists())
        self.assertNotIn("made-by-check", self.call("diff", "demo", "a"))

    def test_the_reviewer_gets_a_frozen_snapshot_of_the_writers_tree(self):
        self.cfg["defaults"]["review"] = "cross-all"
        self.start([{"id": "a", "files": ["a.txt"]}], review="cross-all")
        self.launch("a")
        self.work("a", {"a.txt": "reviewed content\n"})
        self.stop_with_receipt("a")
        out = self.call("review", "demo", "a", "--settle", "0")
        self.assertIn("REVIEW review-a reviewer:reviewer-model", out)
        snap = self.tree("review-a")
        self.assertEqual(Path(self.cwd_of_launch()), snap)
        self.assertNotEqual(snap, self.tree("a"))
        self.assertEqual((snap / "a.txt").read_text(), "reviewed content\n")
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=snap, capture_output=True, text=True)
        self.assertEqual(staged.stdout, "a.txt\n")
        prompt = (self.repo / ".fleet/runs/demo/prompts/review-a.md").read_text()
        self.assertIn(f"You run in `{snap}`, a frozen snapshot", prompt)
        self.assertIn(f"Review writer `a` in `{snap}`", prompt)
        (self.tree("a") / "a.txt").write_text("changed after the review started\n")  # the snapshot does not move
        self.assertEqual((snap / "a.txt").read_text(), "reviewed content\n")
        (self.tree("a") / "a.txt").write_text("reviewed content\n")
        self.report("review-a")
        self.stop_with_receipt("review-a")
        self.accept("a")
        self.assertEqual(self.registered(), [])                 # writer and reviewer trees both removed

    def test_the_hooks_guard_reads_the_worktree(self):
        self.cfg["providers"]["writer"].update(
            launch=HOOKED_LAUNCH, hooks="claude-settings",
            profiles={"worker": {"args": "", "level": "workspace-write"},
                      "review": {"args": "--permission-mode manual", "level": "read-only"}})
        self.start([{"id": "a", "files": ["owned.txt"]}])
        self.launch("a")
        w = self.state()["workers"]["a"]
        scope = json.loads(Path(w["hooks"]["scope"]).read_text())
        self.assertEqual(scope["repo"], str(self.tree("a")))
        settings = json.loads(Path(w["hooks"]["settings"]).read_text())
        [handler] = settings["hooks"]["PreToolUse"][0]["hooks"]

        def guard(path):
            payload = {"session_id": "s", "cwd": str(self.tree("a")), "hook_event_name": "PreToolUse",
                       "tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x"}}
            return subprocess.run(["/bin/sh", "-c", handler["command"]], input=json.dumps(payload), text=True,
                                  capture_output=True, timeout=30).returncode
        self.assertEqual(guard(self.tree("a") / "owned.txt"), 0)
        self.assertEqual(guard(self.repo / "owned.txt"), 2)        # the owner's checkout is outside its scope
        self.assertEqual(guard(self.repo / ".fleet/runs/demo/reports/a.md"), 0)

    def test_the_prompt_names_the_tree_and_lifts_the_build_ban(self):
        self.start([{"id": "a", "files": ["a.txt"]}, {"id": "i", "kind": "investigate", "files": [],
                                                       "checks": [{"manual": "read only"}], "context": "c"}])
        prompt = (self.repo / ".fleet/runs/demo/prompts/a.md").read_text()
        tree = Path(self.state()["options"]["worktree_dir"]) / "a"
        self.assertIn(f"in your own Git worktree at `{tree}`", prompt)
        self.assertIn("You may run builds, tests and other local tooling inside your worktree", prompt)
        self.assertNotIn("Do not run build, dev, start or preview servers", prompt)
        self.assertIn(str(self.repo / ".fleet/runs/demo/reports/a.md"), prompt)
        investigate = (self.repo / ".fleet/runs/demo/prompts/i.md").read_text()
        self.assertIn(f"shared checkout at `{self.repo}`", investigate)  # investigators read the checkout
        self.launch("i")
        self.assertEqual(Path(self.cwd_of_launch()), self.repo)
        self.assertNotIn("i", self.state()["worktrees"])


class Lifecycle(WorktreeCase):
    def test_repair_rounds_and_resume_reuse_the_tickets_own_tree(self):
        self.cfg["providers"]["writer"].update(launch="{bin} {session} --model {model} --effort {effort} {prompt}",
                                               session="--session-id {id}", resume="--resume {id}")
        self.start([{"id": "a", "files": ["a.txt"]}, {"id": "b", "files": ["b.txt"]}])
        self.launch("a")
        tree = self.tree("a")
        self.work("a", {"a.txt": "first round\n"})
        self.stop_with_receipt("a")
        self.launch("a")                                         # a repair round
        self.assertEqual(self.tree("a"), tree)
        self.assertEqual(Path(self.cwd_of_launch()), tree)
        self.assertEqual((tree / "a.txt").read_text(), "first round\n")
        self.receipt("a", returncode=1)
        self.call("status", "demo")
        self.call("launch", "demo", "a", "writer", "--resume")
        self.assertEqual(Path(self.cwd_of_launch()), tree)
        self.launch("b")
        self.assertNotEqual(self.tree("b"), tree)                # another ticket never gets it
        self.assertEqual(len(self.registered()), 2)
        self.assertIn(f"isolation: worktree; trees under {tree.parent} (2 now", self.call("resume", "demo"))

    def test_drop_removes_the_tree_and_keeps_its_changes_as_a_patch(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        tree = self.tree("a")
        self.work("a", {"a.txt": "unfinished\n"})
        self.stop_with_receipt("a")
        out = self.call("drop", "demo", "a", "--reason", "the owner abandoned it")
        patch_file = self.repo / ".fleet/runs/demo/worktrees/a.patch"
        self.assertIn(f"saved in {patch_file}", out)
        self.assertIn(f"REMOVED worktree {tree}", out)
        self.assertIn("nothing it did reached the checkout", out)
        self.assertIn("+unfinished", patch_file.read_text())
        self.assertFalse(tree.exists())
        self.assertEqual(self.registered(), [])
        self.assertEqual(self.state()["worktrees"], {})

    def test_check_lists_trees_and_cleans_orphans(self):
        self.start([{"id": "a", "files": ["a.txt"]}, {"id": "b", "files": ["b.txt"]}])
        self.launch("a")
        self.launch("b")
        state = self.state()
        root = Path(state["options"]["worktree_dir"])
        stray = root / "leftover"
        subprocess.run(["git", "worktree", "add", "--detach", str(stray), "HEAD"], cwd=self.repo, check=True,
                       capture_output=True)
        state["tickets"]["b"]["status"] = "dropped"             # as if drop could not remove its tree
        fleet.save_run(self.repo, "demo", state)
        out = self.call("check", "demo")
        self.assertIn(f"WORKTREE a {self.tree('a')}: in use", out)
        self.assertIn(f"WORKTREE b {self.tree('b')}: orphan: b is dropped", out)
        self.assertIn(f"WORKTREE - {stray}: orphan: not in the run's records", out)
        self.assertIn("NOTE 2 orphan worktree(s): fleet check demo --clean-worktrees removes them", out)
        b_tree = self.tree("b")
        out = self.call("check", "demo", "--clean-worktrees")
        self.assertIn(f"REMOVED worktree {b_tree}", out)
        self.assertIn(f"REMOVED worktree {stray}", out)
        self.assertEqual(self.registered(), [self.tree("a")])
        self.assertEqual(list(self.state()["worktrees"]), ["a"])
        self.assertNotIn("NOTE", self.call("check", "demo"))

    def test_a_missing_tree_is_reported_not_guessed_at(self):
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        self.stop_with_receipt("a")
        subprocess.run(["rm", "-rf", str(self.tree("a"))], check=True)
        self.assertIn("worktree", self.call("diff", "demo", "a", expected=1))
        self.assertIn("missing: a cannot be diffed or merged", self.call("check", "demo"))

    def test_worktree_setup_runs_in_the_new_tree_and_a_failure_launches_nothing(self):
        self.cfg["defaults"]["worktree_setup"] = "echo ready > .setup-done; pwd > .setup-where"
        self.start([{"id": "a", "files": ["a.txt"]}, {"id": "b", "files": ["b.txt"]}])
        self.launch("a")
        self.assertEqual((self.tree("a") / ".setup-where").read_text().strip(), str(self.tree("a")))
        self.assertFalse((self.repo / ".setup-done").exists())
        self.work("a", {"a.txt": "a\n"})
        self.stop_with_receipt("a")
        self.assertNotIn(".setup-done", self.call("diff", "demo", "a"))  # setup output is the tree's base
        state = self.state()
        state["options"]["worktree_setup"] = "echo broken; exit 4"     # the run keeps the value saved at init
        fleet.save_run(self.repo, "demo", state)
        out = self.launch("b", expected=1)
        self.assertIn("worktree_setup `echo broken; exit 4` failed in b's new worktree (exit 4)", out)
        self.assertNotIn("b", self.state()["workers"])
        self.assertNotIn("b", self.state().get("worktrees", {}))
        self.assertFalse((Path(state["options"]["worktree_dir"]) / "b").exists())
        self.assertIn("broken", (self.repo / ".fleet/runs/demo/worktrees/b.setup.log").read_text())


class Placement(WorktreeCase):
    def test_trees_default_to_the_cache_folder_outside_the_checkout(self):
        self.cfg["defaults"]["worktree_root"] = ""
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(cache)])
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache)}):
            self.call("init", "demo", "--workspace", "workspace:1", "--isolation", "worktree")
        where = Path(self.state()["options"]["worktree_dir"])
        self.assertEqual(where.parent.parent, (cache / "agent-fleet" / "worktrees").resolve())
        self.assertEqual(where.name, "demo")
        self.assertTrue(re.fullmatch(r".+-[0-9a-f]{8}", where.parent.name))
        self.assertEqual(self.state()["options"]["isolation"], "worktree")

    def test_init_refuses_trees_inside_the_checkout(self):
        self.cfg["defaults"]["worktree_root"] = str(self.repo / "trees")
        out = self.call("init", "demo", "--workspace", "workspace:1", "--isolation", "worktree", expected=1)
        self.assertIn("inside this checkout", out)
        self.assertFalse((self.repo / ".fleet/runs/demo/run.json").exists())

    def test_config_validates_the_new_keys(self):
        for key, value, message in (("worktree_root", "relative/dir", "absolute path"),
                                    ("worktree_root", 3, "absolute path"), ("worktree_setup", ["npm", "ci"], "shell")):
            with self.subTest(key=key, value=value):
                path = Path(self.temp.name) / f"cfg-{key}.toml"
                path.write_text(f"[defaults]\n{key} = {json.dumps(value)}\n")
                with self.assertRaisesRegex(fleet.FleetError, message):
                    fleet_load(path)
        path = Path(self.temp.name) / "cfg-ok.toml"
        path.write_text('[defaults]\nworktree_root = "~/trees"\nworktree_setup = "npm ci"\n'
                        '[providers.codex]\nadd_dir = "--add-dir"\n')
        with self.assertRaisesRegex(fleet.FleetError, r"\{dir\} slot"):
            fleet_load(path)


def fleet_load(path: Path) -> dict:
    """The real load_config (RuntimeCase patches the module's), read from `path`."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("fleet_worktree_config", str(Path(fleet.__file__)))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    try:
        return module.load_config(path)
    except module.FleetError as e:
        raise fleet.FleetError(str(e)) from None


class Trust(WorktreeCase):
    def test_auto_trust_marks_the_checkout_and_the_tree(self):
        self.cfg["defaults"]["auto_trust"] = True
        self.cfg["providers"]["writer"].update(trust="codex-flag", add_dir="--add-dir {dir}")
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        argv = shlex.split(self.state()["workers"]["a"]["command"])
        tree = self.tree("a")
        self.assertIn('projects={"%s"={trust_level="trusted"},"%s"={trust_level="trusted"}}' % (self.repo, tree), argv)
        dirs = [argv[n + 1] for n, x in enumerate(argv) if x == "--add-dir"]
        self.assertEqual(dirs, [str(self.repo / ".fleet/runs/demo")])            # never the checkout itself
        prompt = (self.repo / ".fleet/runs/demo/prompts/a.md").read_text()
        self.assertIn(f"Read the local ticket: `{self.repo / '.fleet/runs/demo/tickets/a.md'}`", prompt)

    def test_claude_config_trust_records_both_paths(self):
        account = Path(self.temp.name) / "account"
        account.mkdir()
        (account / ".claude.json").write_text("{}")
        self.cfg["defaults"]["auto_trust"] = True
        self.cfg["providers"]["writer"].update(trust="claude-config", account_env="CLAUDE_CONFIG_DIR",
                                               env={"CLAUDE_CONFIG_DIR": str(account)}, add_dir="--add-dir={dir}")
        self.start([{"id": "a", "files": ["a.txt"]}])
        self.launch("a")
        projects = json.loads((account / ".claude.json").read_text())["projects"]
        self.assertTrue(projects[str(self.repo)]["hasTrustDialogAccepted"])
        self.assertTrue(projects[str(self.tree("a"))]["hasTrustDialogAccepted"])
        self.assertIn(f"--add-dir={self.repo / '.fleet/runs/demo'}", self.state()["workers"]["a"]["command"])

    def test_without_auto_trust_launch_warns_and_marks_nothing(self):
        self.cfg["providers"]["writer"].update(trust="codex-flag")
        self.start([{"id": "a", "files": ["a.txt"]}])
        out = self.launch("a")
        self.assertIn("auto_trust is off, so if writer asks whether to trust that folder", out)
        self.assertNotIn("projects=", self.state()["workers"]["a"]["command"])

    def test_the_shipped_adapters_reach_the_run_folder(self):
        shipped = fleet_load(Path("/nonexistent/fleet-config.toml"))
        self.assertEqual(shipped["providers"]["claude"]["add_dir"], "--add-dir={dir}")
        self.assertEqual(shipped["providers"]["claude-co"]["add_dir"], "--add-dir={dir}")
        self.assertEqual(shipped["providers"]["codex"]["add_dir"], "--add-dir {dir}")
        self.assertEqual(shipped["defaults"]["worktree_root"], "")
        self.assertEqual(shipped["defaults"]["worktree_setup"], "")


@unittest.skipUnless(HAS_TMUX, "tmux is not installed")
class LiveWorktrees(FleetCase):
    """Two scripted workers in headless tmux: each really runs with its worktree as its cwd."""

    def test_two_workers_edit_their_own_trees_and_accept_merges_both(self):
        trees = self.root / "trees"
        log = self.root / "log"
        log.mkdir()
        plan = self.root / "behaviour.json"
        def report(i):  # the report template's fleet block, as a worker that ran its check writes it
            data = {"files_changed": [f"{i}.txt"], "checks": [{"run": f"test -f {i}.txt", "exit": 0}],
                    "criteria": [], "findings": [], "questions": []}
            return "```fleet\n" + json.dumps(data) + f"\n```\n\n- {i}.txt"
        plan.write_text(json.dumps({"a": {"edit": {"a.txt": "from a\n"}, "report": report("a")},
                                    "b": {"edit": {"b.txt": "from b\n"}, "report": report("b")}}))
        worker = " ".join(shlex.quote(p) for p in (sys.executable, str(FIXTURES / "scripted_worker.py")))
        env = f'env = {{ FAKE_PLAN = {json.dumps(str(plan))}, FAKE_LOG = {json.dumps(str(log))} }}'
        self.write_config(f'max_parallel = 2\nreview = "off"\nprefer = ["vendora"]\nworktree_root = {json.dumps(str(trees))}\n'
                          f'[providers.vendora]\nenabled = true\nbin = {json.dumps(worker)}\n'
                          'launch = "{bin} --model {model} {prompt}"\nquit = ["/quit", "enter"]\nfamily = "openai"\n'
                          f'{env}\nmax = 2\ntiers.standard = {{ model = "a-mid" }}\n')
        (self.repo / "a.txt").write_text("base a\n")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "a")
        (self.repo / "owner.txt").write_text("owner's untracked file\n")
        index = (self.repo / ".git/index").read_bytes()
        tickets = self.root / "tickets"
        tickets.mkdir()
        for i in ("a", "b"):
            (tickets / f"{i}.md").write_text(f"# {i}\n")
        plan_file = self.root / "plan.json"
        plan_file.write_text(json.dumps({"tickets": [
            {"id": i, "ticket": str(tickets / f"{i}.md"), "files": [f"{i}.txt"], "provider": "vendora",
             "context": "tiny", "checks": [{"run": f"test -f {i}.txt"}]} for i in ("a", "b")]}))
        self.fleet("init", "live", "--isolation", "worktree")
        self.fleet("plan", "live", "--file", str(plan_file))
        out = self.fleet("launch", "live", "--ready")
        self.assertIn("LAUNCHED a", out)
        self.assertIn("LAUNCHED b", out)
        state = json.loads((self.repo / ".fleet/runs/live/run.json").read_text())
        for i in ("a", "b"):
            self.wait_for("live", rf"^(REPORT|PENDING) {i}\b")  # one wake may carry both
        for i in ("a", "b"):
            tree = Path(state["worktrees"][i]["path"])
            self.assertEqual((tree / f"{i}.txt").read_text(), f"from {i}\n")   # written relative to its cwd
            self.assertEqual((tree / "owner.txt").read_text(), "owner's untracked file\n")
        self.assertEqual((self.repo / "a.txt").read_text(), "base a\n")
        notes = self.root / "notes.md"
        notes.write_text("Checked each tree's diff.\n")
        for i in ("a", "b"):
            out = self.fleet("accept", "live", i, "--gate", "--evidence", str(notes))
            self.assertIn(f"MERGED {i}: applied 1 file(s)", out)
            self.assertIn(f"GATE {i} 1/1 passed, scope ok", out)
        self.assertEqual((self.repo / "a.txt").read_text(), "from a\n")
        self.assertEqual((self.repo / "b.txt").read_text(), "from b\n")
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertIn("OK: captured HEAD and index states match the run baseline", self.fleet("check", "live"))
        self.assertNotIn(str(trees), self.git("worktree", "list"))


if __name__ == "__main__":
    unittest.main()
