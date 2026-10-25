#!/usr/bin/env python3
"""Hold a POSIX file lock for the lifetime of the Duet kiosk launcher.

The lock stays in this supervising process. It is not inherited by Firefox,
the proxy, or ssh, so it is released if the launcher is terminated.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: launcher_lock.py /path/to/launch.sh [args...]", file=sys.stderr)
        return 2

    state_dir = Path.home() / ".local" / "state" / "drive-console"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "launcher.lock"
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Drive Console is already running; not starting another kiosk.")
            return 0

        env = dict(os.environ, DRIVE_CONSOLE_LAUNCHER_CHILD="1")
        child = subprocess.Popen(
            ["/bin/bash", *sys.argv[1:]],
            env=env,
            start_new_session=True,
            close_fds=True,
        )

        def forward(signum: int, _frame: object) -> None:
            # Terminating only the child does not necessarily stop launch.sh:
            # Firefox can exit and the kiosk's restart loop can reopen it.
            # Bound shutdown and terminate the whole private process group.
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, forward)
        signal.signal(signal.SIGINT, forward)
        signal.signal(signal.SIGHUP, forward)
        try:
            return child.wait()
        finally:
            if child.poll() is None:
                forward(signal.SIGTERM, None)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    forward(signal.SIGKILL, None)
                    child.wait()


if __name__ == "__main__":
    sys.exit(main())
