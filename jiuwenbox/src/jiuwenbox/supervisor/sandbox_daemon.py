# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Long-running in-sandbox daemon used to hold a sandbox lifecycle open."""

from __future__ import annotations

import signal
import threading

SANDBOX_DAEMON_SANDBOX_PATH = "/jiuwenbox-sandbox-daemon.py"
SANDBOX_DAEMON_COMMAND = ["/usr/bin/python3", SANDBOX_DAEMON_SANDBOX_PATH]

_SHUTDOWN_EVENT = threading.Event()


def _handle_shutdown(_signum: int, _frame: object) -> None:
    _SHUTDOWN_EVENT.set()


def main() -> int:
    """Stay alive until the runtime asks the sandbox to stop."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    _SHUTDOWN_EVENT.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
