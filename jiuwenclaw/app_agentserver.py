# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Standalone AgentServer entrypoint.

This process only starts:
- JiuWenClaw (agent runtime)
- AgentWebSocketServer (ws server for Gateway)

Gateway should be started separately and connect to this ws server.
Both processes share the same user workspace directory (~/.jiuwenclaw).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from dotenv import load_dotenv

from jiuwenclaw.utils import USER_WORKSPACE_DIR, get_env_file, prepare_workspace, logger


# Ensure workspace initialized
_config_file = USER_WORKSPACE_DIR / "config" / "config.yaml"
if not _config_file.exists():
    prepare_workspace(overwrite=False)

# Load env from user workspace config/.env
load_dotenv(dotenv_path=get_env_file())


class _NopCronScheduler:
    """A no-op scheduler placeholder for CronController.

    In split deployment, AgentServer only provides cron CRUD storage.
    Actual scheduling/triggering is handled by the Gateway process.
    """

    async def reload(self) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @staticmethod
    def is_running() -> bool:
        return False


async def _run(host: str, port: int) -> None:
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.gateway import AgentWebSocketServer
    from jiuwenclaw.gateway.cron import CronController, CronJobStore

    logger.info("[AgentServer] starting: ws://%s:%s", host, port)

    cron_store = CronJobStore()
    CronController.get_instance(store=cron_store, scheduler=_NopCronScheduler())

    agent = JiuWenClaw()
    server = AgentWebSocketServer.get_instance(
        agent=agent,
        host=host,
        port=port,
        ping_interval=20.0,
        ping_timeout=20.0,
    )
    await server.start()

    # create_instance depends on CronController singleton being initialized
    await agent.create_instance()

    logger.info("[AgentServer] ready: ws://%s:%s  Ctrl+C to stop", host, port)

    stop_event = asyncio.Event()

    def _on_signal() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        import signal

        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except (NotImplementedError, OSError):
        pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("[AgentServer] stopping…")
        await server.stop()
        logger.info("[AgentServer] stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-agentserver",
        description="Start JiuwenClaw AgentServer (standalone process for Gateway to connect).",
    )
    parser.add_argument(
        "--host",
        "-H",
        default=None,
        metavar="HOST",
        help="Bind host (default: AGENT_SERVER_HOST env or 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        metavar="PORT",
        help="Bind port (default: AGENT_SERVER_PORT env or 18092).",
    )
    args = parser.parse_args()

    host = args.host or os.getenv("AGENT_SERVER_HOST", "127.0.0.1")
    port = args.port or int(os.getenv("AGENT_SERVER_PORT", "18092"))

    asyncio.run(_run(host=host, port=port))


if __name__ == "__main__":
    main()

