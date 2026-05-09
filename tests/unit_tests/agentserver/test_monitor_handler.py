# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.agents.harness.team.monitor_handler import TeamMonitorHandler


class _FakeMember:
    def __init__(self, member_id: str):
        self.member_id = member_id

    def model_dump(self) -> dict[str, str]:
        return {"member_id": self.member_id}


class _FakeMonitor:
    def __init__(self, members: list[_FakeMember], leader_id: str | None):
        self.team_id = "team-1"
        self._members = members
        self._leader_id = leader_id

    async def get_members(self) -> list[_FakeMember]:
        return list(self._members)

    async def get_team_info(self):
        if self._leader_id is None:
            return None
        return SimpleNamespace(leader_id=self._leader_id)


@pytest.mark.anyio
async def test_get_team_snapshot_filters_leader_member() -> None:
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[_FakeMember("team_leader"), _FakeMember("worker-1")],
            leader_id="team_leader",
        ),
        "sess-1",
    )

    snapshot = await handler.get_team_snapshot()

    assert snapshot == {
        "members": [{"member_id": "worker-1"}],
        "team_id": "team-1",
    }


@pytest.mark.anyio
async def test_get_team_snapshot_keeps_members_when_team_info_unavailable() -> None:
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[_FakeMember("worker-1"), _FakeMember("worker-2")],
            leader_id=None,
        ),
        "sess-2",
    )

    snapshot = await handler.get_team_snapshot()

    assert snapshot == {
        "members": [{"member_id": "worker-1"}, {"member_id": "worker-2"}],
        "team_id": "team-1",
    }
