# coding: utf-8
from __future__ import annotations

from jiuwenclaw.agentserver.deep_agent.security_review.scheduler import SecurityReviewScheduler
from jiuwenclaw.agentserver.deep_agent.security_review.schema import (
    FailureClass,
    ReviewRequest,
    SecurityReviewConfig,
    SecuritySignal,
    Severity,
)


def _request(session_id: str, priority: Severity, key: tuple[str, ...]) -> ReviewRequest:
    return ReviewRequest(
        request_type="timely_tool_failure_review",
        session_id=session_id,
        priority=priority,
        dedupe_key=key,
        signals=[
            SecuritySignal(
                signal_type="repeated_tool_failure",
                severity=priority,
                session_id=session_id,
                tool_name="read_file",
                failure_class=FailureClass.CROSS_WORKSPACE_DENIED,
            )
        ],
    )


def test_scheduler_accepts_first_timely_review():
    scheduler = SecurityReviewScheduler(SecurityReviewConfig(async_queue_size=1))

    accepted = scheduler.schedule(_request("s1", Severity.HIGH, ("s1", "read_file", "cross")))

    assert accepted is True
    assert len(scheduler.drain()) == 1


def test_scheduler_deduplicates_same_repeated_failure():
    scheduler = SecurityReviewScheduler(SecurityReviewConfig(async_queue_size=2))
    req = _request("s1", Severity.HIGH, ("s1", "read_file", "cross"))

    assert scheduler.schedule(req) is True
    assert scheduler.schedule(req) is False
    assert len(scheduler.drain()) == 1


def test_high_priority_replaces_low_priority_when_queue_full():
    scheduler = SecurityReviewScheduler(SecurityReviewConfig(async_queue_size=1))
    low = _request("s1", Severity.MEDIUM, ("s1", "session-end"))
    high = _request("s1", Severity.HIGH, ("s1", "read_file", "cross"))

    assert scheduler.schedule(low) is True
    assert scheduler.schedule(high) is True

    drained = scheduler.drain()
    assert drained == [high]


def test_full_queue_rejects_equal_or_lower_priority_without_mutating_queue():
    equal_scheduler = SecurityReviewScheduler(SecurityReviewConfig(async_queue_size=1))
    high = _request("s1", Severity.HIGH, ("s1", "read_file", "cross"))
    equal_high = _request("s1", Severity.HIGH, ("s1", "shell", "cross"))

    assert equal_scheduler.schedule(high) is True
    assert equal_scheduler.schedule(equal_high) is False
    assert equal_scheduler.drain() == [high]

    lower_scheduler = SecurityReviewScheduler(SecurityReviewConfig(async_queue_size=1))
    medium = _request("s1", Severity.MEDIUM, ("s1", "session-end"))

    assert lower_scheduler.schedule(high) is True
    assert lower_scheduler.schedule(medium) is False
    assert lower_scheduler.drain() == [high]


def test_full_queue_replaces_lowest_priority_item_not_first_lower_priority_item():
    scheduler = SecurityReviewScheduler(SecurityReviewConfig(async_queue_size=2))
    high = _request("s1", Severity.HIGH, ("s1", "read_file", "cross"))
    low = _request("s1", Severity.LOW, ("s1", "session-end"))
    critical = _request("s1", Severity.CRITICAL, ("s1", "shell", "cross"))

    assert scheduler.schedule(high) is True
    assert scheduler.schedule(low) is True
    assert scheduler.schedule(critical) is True

    assert scheduler.drain() == [high, critical]


def test_scheduler_enforces_max_reviews_per_session():
    scheduler = SecurityReviewScheduler(SecurityReviewConfig(max_reviews_per_session=1))

    assert scheduler.mark_review_started("s1") is True
    assert scheduler.mark_review_started("s1") is False
