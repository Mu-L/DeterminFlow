"""Entrypoint for the single local Workflow Executor process."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from .executor_process import watch_parent_exit
from .executor_protocol import ExecutorIdentity
from .executor_transport import LoopbackEndpoint, take_auth_token_from_env


logger = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-endpoint-path", type=Path, required=True)
    parser.add_argument("--executor-id", required=True)
    parser.add_argument("--executor-epoch", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--lease-path", type=Path, required=True)
    parser.add_argument("--event-host", required=True)
    parser.add_argument("--event-port", type=int, required=True)
    return parser


async def _run(args: argparse.Namespace) -> None:
    if os.getenv("DETERMINFLOW_RUNTIME_ROLE") != "workflow-executor":
        raise RuntimeError("Workflow Executor runtime role is not set")
    auth_token = take_auth_token_from_env()

    # Import only after the role is fixed. web_server creates its application at
    # module import time and the lifespan uses this role to disable Controller
    # responsibilities.
    from src.web_server import app, lifespan
    from src.workflow.executor_lease import ExecutorProcessLease
    from src.workflow.executor_events import ExecutorEventForwarder
    from src.web.event_bus import event_bus
    from src.workflow.executor_server import WorkflowExecutorServer

    identity = ExecutorIdentity(args.executor_id, args.executor_epoch)
    event_endpoint = LoopbackEndpoint(args.event_host, args.event_port)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    lease = ExecutorProcessLease(args.lease_path)
    await asyncio.to_thread(lease.acquire, 55.0)
    event_forwarder = ExecutorEventForwarder(
        event_endpoint, identity, auth_token=auth_token,
    )
    event_bus.set_process_forwarder(event_forwarder.emit)
    try:
        async with lifespan(app):
            manager = app.state.workflow_manager
            manager.set_local_executor_identity(identity)
            server = WorkflowExecutorServer(
                identity,
                manager,
                auth_token=auth_token,
                endpoint_path=args.rpc_endpoint_path,
                shutdown_callback=stop_event.set,
                event_forwarder=event_forwarder,
            )
            await server.start()
            parent_watch = asyncio.create_task(
                watch_parent_exit(args.parent_pid, stop_event),
                name="workflow-executor-parent-watch",
            )
            logger.info(
                "Workflow Executor ready: id=%s epoch=%s pid=%s",
                identity.executor_id,
                identity.epoch,
                os.getpid(),
            )
            try:
                await stop_event.wait()
            finally:
                parent_watch.cancel()
                await asyncio.gather(parent_watch, return_exceptions=True)
                await server.close()
    finally:
        event_bus.set_process_forwarder(None)
        await event_forwarder.close()
        lease.release()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
