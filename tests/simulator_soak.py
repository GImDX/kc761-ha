"""Local-only simulator soak acceptance runner.

This runner intentionally exercises only the in-process TCP simulator.  It
never accepts a device endpoint or a remote host, and the client requests are
limited to the existing read-only device-info/status methods.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import ipaddress
import json
import math
from pathlib import Path
import time
from collections.abc import Awaitable, Callable

from custom_components.kc761.const import CMD_GET_DEVICE_INFO, CMD_GET_STA
from custom_components.kc761.protocol.client import KC761Client
from tests.simulator import Kc761Simulator, SimulatorScenario


MIN_SOAK_SECONDS = 600.0
MIN_RECONNECTS = 100
DEFAULT_OPERATION_TIMEOUT = 5.0
LOOPBACK_HOST = "127.0.0.1"
ALLOWED_READ_COMMANDS = frozenset({CMD_GET_DEVICE_INFO, CMD_GET_STA})


class SoakError(RuntimeError):
    """A bounded local simulator soak assertion failed."""


@dataclass(frozen=True, slots=True)
class SoakResult:
    """Machine-readable evidence produced after a successful soak."""

    schema: int
    status: str
    host: str
    requested_duration_seconds: float
    requested_reconnects: int
    elapsed_seconds: float
    completed_reconnects: int
    simulator_connections: int
    received_command_count: int
    received_commands: tuple[int, ...]
    forbidden_commands: tuple[int, ...]
    client_connected: bool
    transport_connected: bool
    reader_task_present: bool
    pending_transactions: int
    leaked_task_names: tuple[str, ...]


def validate_loopback_host(host: str) -> str:
    """Accept only a literal IPv4/IPv6 loopback address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as err:
        raise ValueError("simulator host must be a literal loopback address") from err
    if not address.is_loopback:
        raise ValueError("simulator host must be a loopback address")
    return str(address)


def validate_acceptance_parameters(
    duration_seconds: float,
    reconnects: int,
    operation_timeout: float,
) -> None:
    """Validate the non-negotiable acceptance floor and bounded I/O timeout."""
    if not math.isfinite(duration_seconds) or duration_seconds < MIN_SOAK_SECONDS:
        raise ValueError(f"duration must be at least {MIN_SOAK_SECONDS:g} seconds")
    if reconnects < MIN_RECONNECTS:
        raise ValueError(f"reconnects must be at least {MIN_RECONNECTS}")
    if not math.isfinite(operation_timeout) or not 0 < operation_timeout <= 60:
        raise ValueError("operation timeout must be in (0, 60] seconds")


def _pending_task_names(baseline: set[asyncio.Task[object]]) -> tuple[str, ...]:
    """Return newly-created unfinished tasks, excluding the current runner."""
    current = asyncio.current_task()
    return tuple(
        sorted(
            task.get_name()
            for task in asyncio.all_tasks()
            if task is not current and task not in baseline and not task.done()
        )
    )


async def _wait_for_remote_close(client: KC761Client) -> None:
    """Wait until the simulator's close-after-response EOF reaches the client."""
    while client.connected:
        await asyncio.sleep(0.005)


async def _bounded_request(
    operation: Awaitable[object],
    timeout: float,
) -> object:
    """Apply one bounded timeout to a simulator request."""
    return await asyncio.wait_for(operation, timeout=timeout)


def _assert_soak_invariants(
    result: SoakResult,
    *,
    requested_duration: float,
    requested_reconnects: int,
) -> None:
    """Enforce count, safety, duration, and cleanup gates on a result."""
    if result.elapsed_seconds < requested_duration:
        raise SoakError(
            f"measured duration {result.elapsed_seconds:.3f}s is below "
            f"{requested_duration:.3f}s"
        )
    if result.completed_reconnects < requested_reconnects:
        raise SoakError(
            f"completed {result.completed_reconnects} reconnects, "
            f"expected {requested_reconnects}"
        )
    if result.simulator_connections != result.completed_reconnects + 1:
        raise SoakError(
            "simulator connection count does not equal initial connection plus "
            "completed reconnects"
        )
    if result.forbidden_commands:
        raise SoakError(f"simulator observed forbidden commands: {result.forbidden_commands}")
    if any(command not in ALLOWED_READ_COMMANDS for command in result.received_commands):
        raise SoakError("simulator observed a command outside the read-only allowlist")
    if result.client_connected or result.transport_connected:
        raise SoakError("client connection remained open after cleanup")
    if result.reader_task_present or result.pending_transactions:
        raise SoakError("client reader or pending transactions remained after cleanup")
    if result.leaked_task_names:
        raise SoakError(f"unfinished soak tasks remained: {result.leaked_task_names}")


async def run_soak(
    *,
    host: str = LOOPBACK_HOST,
    duration_seconds: float = MIN_SOAK_SECONDS,
    reconnects: int = MIN_RECONNECTS,
    operation_timeout: float = DEFAULT_OPERATION_TIMEOUT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    progress: Callable[[str], None] | None = print,
) -> SoakResult:
    """Run the 10-minute/100-reconnect local acceptance soak."""
    normalized_host = validate_loopback_host(host)
    validate_acceptance_parameters(duration_seconds, reconnects, operation_timeout)
    baseline_tasks = set(asyncio.all_tasks())
    scenario = SimulatorScenario(close_after_response=True)
    simulator = Kc761Simulator(scenario)
    simulator.host = normalized_host
    client: KC761Client | None = None
    started = clock()
    completed = 0
    try:
        async with simulator:
            if validate_loopback_host(simulator.host) != normalized_host:
                raise SoakError("simulator did not bind the requested loopback address")
            client = KC761Client(
                simulator.host,
                simulator.port,
                request_timeout=operation_timeout,
            )
            info = await _bounded_request(client.async_get_device_info(), operation_timeout)
            if getattr(info, "device_id", None) != "7601-0000-000001":
                raise SoakError("simulator device-info response is unexpected")
            await asyncio.wait_for(_wait_for_remote_close(client), timeout=operation_timeout)
            if simulator.connections != 1:
                raise SoakError("initial simulator connection was not observed")

            deadline = started + duration_seconds
            while completed < reconnects:
                connections_before = simulator.connections
                status = await _bounded_request(client.async_get_status(), operation_timeout)
                if getattr(status, "battery_percent", None) != 87:
                    raise SoakError("simulator status response is unexpected")
                await asyncio.wait_for(_wait_for_remote_close(client), timeout=operation_timeout)
                if simulator.connections != connections_before + 1:
                    raise SoakError("reconnect did not create exactly one simulator connection")
                completed += 1
                if progress is not None and (completed == 1 or completed % 10 == 0):
                    progress(
                        f"SOAK_PROGRESS reconnects={completed}/{reconnects} "
                        f"elapsed={clock() - started:.3f}s"
                    )
                scheduled = started + duration_seconds * completed / reconnects
                delay = scheduled - clock()
                if delay > 0:
                    await sleep(delay)

            remaining = deadline - clock()
            if remaining > 0:
                await sleep(remaining)
            elapsed = clock() - started
            await client.async_close()
            result = SoakResult(
                schema=1,
                status="passed",
                host=normalized_host,
                requested_duration_seconds=duration_seconds,
                requested_reconnects=reconnects,
                elapsed_seconds=elapsed,
                completed_reconnects=completed,
                simulator_connections=simulator.connections,
                received_command_count=len(simulator.received_commands),
                received_commands=tuple(simulator.received_commands),
                forbidden_commands=tuple(simulator.forbidden_commands),
                client_connected=client.connected,
                transport_connected=bool(getattr(client.transport, "connected", False)),
                reader_task_present=client._reader_task is not None,
                pending_transactions=len(client._pending),
                leaked_task_names=(),
            )
        await asyncio.sleep(0)
        result = SoakResult(
            **{
                **asdict(result),
                "leaked_task_names": _pending_task_names(baseline_tasks),
            }
        )
        _assert_soak_invariants(
            result,
            requested_duration=duration_seconds,
            requested_reconnects=reconnects,
        )
        return result
    finally:
        if client is not None:
            await client.async_close()


def _write_output(path: str | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    output = Path(path)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=LOOPBACK_HOST)
    parser.add_argument("--duration-seconds", type=float, default=MIN_SOAK_SECONDS)
    parser.add_argument("--reconnects", type=int, default=MIN_RECONNECTS)
    parser.add_argument("--operation-timeout", type=float, default=DEFAULT_OPERATION_TIMEOUT)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run_soak(
                host=args.host,
                duration_seconds=args.duration_seconds,
                reconnects=args.reconnects,
                operation_timeout=args.operation_timeout,
            )
        )
    except Exception as err:
        payload = {"schema": 1, "status": "failed", "error": str(err)}
        _write_output(args.output, payload)
        print("SOAK_ERROR=" + str(err), flush=True)
        return 2
    payload = asdict(result)
    _write_output(args.output, payload)
    print("SOAK_RESULT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
    if args.output:
        print(f"SOAK_OUTPUT={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
