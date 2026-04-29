# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded security review scheduling."""
from __future__ import annotations

from collections import Counter, deque

from jiuwenclaw.agentserver.deep_agent.security_review.schema import (
    ReviewRequest,
    SecurityReviewConfig,
    Severity,
)

_RANK = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class SecurityReviewScheduler:
    """Deduplicates and bounds review requests."""

    def __init__(self, config: SecurityReviewConfig) -> None:
        self.config = config
        self._queue: deque[ReviewRequest] = deque()
        self._dedupe: set[tuple[str, ...]] = set()
        self._started_counts: Counter[str] = Counter()

    def schedule(self, request: ReviewRequest) -> bool:
        if request.dedupe_key in self._dedupe:
            return False
        limit = max(1, self.config.async_queue_size)
        if len(self._queue) >= limit:
            if not self._replace_lower_priority(request):
                return False
        self._queue.append(request)
        self._dedupe.add(request.dedupe_key)
        return True

    def drain(self) -> list[ReviewRequest]:
        items = list(self._queue)
        self._queue.clear()
        self._dedupe.clear()
        return items

    def mark_review_started(self, session_id: str) -> bool:
        if self._started_counts[session_id] >= max(1, self.config.max_reviews_per_session):
            return False
        self._started_counts[session_id] += 1
        return True

    def _replace_lower_priority(self, request: ReviewRequest) -> bool:
        incoming_rank = _RANK[request.priority]
        lower_priority_requests = [
            existing for existing in self._queue if _RANK[existing.priority] < incoming_rank
        ]
        if not lower_priority_requests:
            return False
        lowest_priority_request = min(
            lower_priority_requests,
            key=lambda existing: _RANK[existing.priority],
        )
        self._queue.remove(lowest_priority_request)
        self._dedupe.discard(lowest_priority_request.dedupe_key)
        return True
