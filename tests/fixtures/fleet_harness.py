"""Shared by the live tmux tests that drive `skill/bin/fleet` as a subprocess, the way a lead runs it.

Each test gets a throwaway Git repository, its own HOME and FLEET_CONFIG, and a private tmux server (`tmux -L
<socket>`) that is killed on cleanup, whether the test passed or not. Nothing reads the real user config, account
directories or tmux server.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLEET = ROOT / "skill" / "bin" / "fleet"
FIXTURES = Path(__file__).resolve().parent
HAS_TMUX = shutil.which("tmux") is not None
# The lead's own markers, a terminal multiplexer it runs in and any Fleet config never reach the fleet under test.
SCRUB = re.compile(r"(CLAUDE|CODEX|CMUX|TMUX|FLEET)")


class FleetCase(unittest.TestCase):
    """A repo at self.repo (one committed file, a.txt), run commands with self.fleet(...)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "a.txt").write_text("base\n")
        self.git("add", "a.txt")
        self.git("commit", "-qm", "base")
        self.socket = f"fleettest{os.getpid()}{id(self) % 100000}"
        # Registered after the temp dir, so it runs first: no tmux server outlives its test.
        self.addCleanup(subprocess.run, ["tmux", "-L", self.socket, "kill-server"], capture_output=True)
        self.config = self.root / "config.toml"
        self.env = {k: v for k, v in os.environ.items() if not SCRUB.match(k)}
        self.env.update(HOME=str(self.home), FLEET_CONFIG=str(self.config), SHELL="/bin/sh",
                        GIT_CONFIG_NOSYSTEM="1", XDG_CACHE_HOME=str(self.root / "cache"))

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def fleet(self, *args: str, expected: int | None = 0, timeout: float = 60) -> str:
        res = subprocess.run([sys.executable, str(FLEET), *args], cwd=self.repo, env=self.env, text=True,
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        out = res.stdout + res.stderr
        if expected is not None:
            self.assertEqual(res.returncode, expected, f"fleet {' '.join(args)} exited {res.returncode}:\n{out}"
                             f"{self.diagnostics()}")
        return out

    def diagnostics(self) -> str:
        """What a failure message needs: every worker window's screen."""
        res = subprocess.run(["tmux", "-L", self.socket, "list-windows", "-a", "-F", "#{window_id} #{window_name}"],
                             capture_output=True, text=True)
        out = []
        for line in res.stdout.splitlines():
            window = line.split()[0]
            screen = subprocess.run(["tmux", "-L", self.socket, "capture-pane", "-p", "-J", "-S", "-200", "-t", window],
                                    capture_output=True, text=True).stdout
            screen = "\n".join(row for row in screen.splitlines() if row.strip())
            out.append(f"--- window {line}\n{screen}")
        return ("\n" + "\n".join(out)) if out else ""

    def wait_for(self, run: str, pattern: str, timeout: int = 20) -> str:
        """fleet wait until its output matches pattern (other wakes are skipped), with short, test-sized timings."""
        deadline, seen = time.monotonic() + timeout, []
        while time.monotonic() < deadline:
            out = self.fleet("wait", run, "--timeout", "5", "--interval", "1", "--settle", "0", "--stall", "0",
                             expected=None)
            seen.append(out)
            if re.search(pattern, out, re.M):
                return out
            if re.search(r"^(EXITED|UNREACHABLE)", out, re.M):
                break
        self.fail(f"no {pattern!r} from fleet wait {run}:\n" + "".join(seen) + self.diagnostics())

    def write_config(self, text: str) -> None:
        self.config.write_text(f'[defaults]\nbackend = "tmux"\ntmux_socket = "{self.socket}"\n' + text)
