"""Focused regression tests for the local simulator soak runner."""

from __future__ import annotations

import asyncio

import pytest

from tests.simulator_soak import (
    SoakError,
    SoakResult,
    _assert_soak_invariants,
    validate_acceptance_parameters,
    validate_loopback_host,
)


def test_loopback_guard_accepts_literal_loopback_only() -> None:
    assert validate_loopback_host("127.0.0.1") == "127.0.0.1"
    assert validate_loopback_host("::1") == "::1"
    with pytest.raises(ValueError, match="loopback"):
        validate_loopback_host("192.168.8.29")
    with pytest.raises(ValueError, match="literal"):
        validate_loopback_host("localhost")


def test_acceptance_floor_rejects_short_duration_or_reconnect_count() -> None:
    with pytest.raises(ValueError, match="600"):
        validate_acceptance_parameters(599.9, 100, 5)
    with pytest.raises(ValueError, match="100"):
        validate_acceptance_parameters(600, 99, 5)
    with pytest.raises(ValueError, match="operation timeout"):
        validate_acceptance_parameters(600, 100, 0)


def _result(**overrides: object) -> SoakResult:
    values: dict[str, object] = {
        "schema": 1,
        "status": "passed",
        "host": "127.0.0.1",
        "requested_duration_seconds": 600.0,
        "requested_reconnects": 100,
        "elapsed_seconds": 600.1,
        "completed_reconnects": 100,
        "simulator_connections": 101,
        "received_command_count": 101,
        "received_commands": (0x54, *([0x53] * 100)),
        "forbidden_commands": (),
        "client_connected": False,
        "transport_connected": False,
        "reader_task_present": False,
        "pending_transactions": 0,
        "leaked_task_names": (),
    }
    values.update(overrides)
    return SoakResult(**values)


def test_cleanup_and_count_invariants_have_failure_signals() -> None:
    _assert_soak_invariants(_result(), requested_duration=600, requested_reconnects=100)
    with pytest.raises(SoakError, match="reconnects"):
        _assert_soak_invariants(
            _result(completed_reconnects=99, simulator_connections=100),
            requested_duration=600,
            requested_reconnects=100,
        )
    with pytest.raises(SoakError, match="reader"):
        _assert_soak_invariants(
            _result(reader_task_present=True),
            requested_duration=600,
            requested_reconnects=100,
        )


def test_cleanup_task_snapshot_excludes_baseline_and_current_task() -> None:
    async def exercise() -> None:
        from tests.simulator_soak import _pending_task_names

        baseline = set(asyncio.all_tasks())
        assert _pending_task_names(baseline) == ()

    asyncio.run(exercise())
