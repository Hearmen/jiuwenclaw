# coding: utf-8
from __future__ import annotations

from jiuwenclaw.agentserver.deep_agent.security_review.schema import (
    FailureClass,
    SecurityEvent,
    SecurityReviewConfig,
    SecuritySignal,
    Severity,
)
from jiuwenclaw.agentserver.deep_agent.security_review.session_state import (
    SecuritySessionState,
)


def test_ring_buffer_keeps_latest_events():
    state = SecuritySessionState(SecurityReviewConfig(ring_buffer_size=2))

    state.record_event(SecurityEvent("tool_call", "s1", iteration=1))
    state.record_event(SecurityEvent("tool_call", "s1", iteration=2))
    state.record_event(SecurityEvent("tool_call", "s1", iteration=3))

    assert [event.iteration for event in state.snapshot_events("s1")] == [2, 3]


def test_high_risk_signal_creates_consumable_advice():
    state = SecuritySessionState(SecurityReviewConfig())
    signal = SecuritySignal(
        signal_type="dangerous_command",
        severity=Severity.HIGH,
        session_id="s1",
        tool_name="bash",
        evidence="curl | sh",
    )

    state.record_signals([signal])

    advice = state.consume_advice("s1")
    assert advice is not None
    assert "安全监督提示" in advice.content
    assert state.consume_advice("s1") is None


def test_repeated_tool_failure_creates_advice_and_request():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        iteration=1,
        tool_name="read_file",
        failure_class=FailureClass.CROSS_WORKSPACE_DENIED,
        evidence="/Users/alice/private.txt",
    )

    first = state.record_signals([signal])
    second = state.record_signals([signal])

    assert first == []
    assert len(second) == 1
    assert second[0].signal_type == "repeated_tool_failure"
    assert second[0].failure_class == FailureClass.CROSS_WORKSPACE_DENIED
    assert "read_file" in state.consume_advice("s1").content


def test_repeated_failures_are_counted_by_tool_and_failure_class():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    permission = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )
    sandbox = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.SANDBOX_DENIED,
    )

    state.record_signals([permission])
    result = state.record_signals([sandbox])

    assert result == []


def test_post_threshold_repeated_failure_recreates_advice_after_consumed():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )

    state.record_signals([signal])
    threshold = state.record_signals([signal])
    assert len(threshold) == 1
    assert state.consume_advice("s1") is not None

    repeated = state.record_signals([signal])

    assert len(repeated) == 1
    assert repeated[0].signal_type == "repeated_tool_failure"
    assert state.consume_advice("s1") is not None


def test_repeated_failure_counts_are_isolated_by_session():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    s1_signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )
    s2_signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s2",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )

    state.record_signals([s1_signal])
    result = state.record_signals([s2_signal])

    assert result == []
    assert state.counter_snapshot("s1") == {"read_file:permission_denied": 1}
    assert state.counter_snapshot("s2") == {"read_file:permission_denied": 1}


def test_max_session_eviction_removes_old_counters_events_and_advice():
    state = SecuritySessionState(SecurityReviewConfig(max_sessions=1))
    old_signal = SecuritySignal(
        signal_type="dangerous_command",
        severity=Severity.HIGH,
        session_id="old",
        tool_name="bash",
        failure_class=FailureClass.PERMISSION_DENIED,
    )

    state.record_event(SecurityEvent("tool_call", "old", iteration=1))
    state.record_signals([old_signal])
    state.record_event(SecurityEvent("tool_call", "new", iteration=2))

    assert state.snapshot_events("old") == []
    assert state.counter_snapshot("old") == {}
    assert state.consume_advice("old") is None
    assert [event.iteration for event in state.snapshot_events("new")] == [2]
