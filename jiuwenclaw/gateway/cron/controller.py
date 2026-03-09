from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from jiuwenclaw.gateway.cron.scheduler import CronSchedulerService, _cron_next_push_dt
from jiuwenclaw.gateway.cron.store import CronJobStore


class CronController:
    """High-level cron API used by WebChannel handlers."""

    def __init__(self, *, store: CronJobStore, scheduler: CronSchedulerService) -> None:
        self._store = store
        self._scheduler = scheduler

    @staticmethod
    def _validate_schedule(*, cron_expr: str, timezone: str) -> None:
        tz = ZoneInfo(timezone)
        base = datetime.now(tz=tz)
        _ = _cron_next_push_dt(cron_expr, base)

    async def list_jobs(self) -> list[dict[str, Any]]:
        jobs = await self._store.list_jobs()
        return [j.to_dict() for j in jobs]

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = await self._store.get_job(job_id)
        return job.to_dict() if job else None

    async def create_job(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "").strip()
        cron_expr = str(params.get("cron_expr") or "").strip()
        timezone = str(params.get("timezone") or "").strip()
        enabled = bool(params.get("enabled", True))
        description = str(params.get("description") or "")
        wake_offset_seconds = params.get("wake_offset_seconds", None)
        targets = params.get("targets") or []

        self._validate_schedule(cron_expr=cron_expr, timezone=timezone)

        job = await self._store.create_job(
            name=name,
            cron_expr=cron_expr,
            timezone=timezone,
            enabled=enabled,
            wake_offset_seconds=int(wake_offset_seconds) if wake_offset_seconds is not None else None,
            description=description,
            targets=targets,
        )
        await self._scheduler.reload()
        return job.to_dict()

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        patch = dict(patch or {})
        if "cron_expr" in patch or "timezone" in patch:
            existing = await self._store.get_job(job_id)
            if existing is None:
                raise KeyError("job not found")
            cron_expr = str(patch.get("cron_expr") or existing.cron_expr).strip()
            timezone = str(patch.get("timezone") or existing.timezone).strip()
            self._validate_schedule(cron_expr=cron_expr, timezone=timezone)

        job = await self._store.update_job(job_id, patch)
        await self._scheduler.reload()
        return job.to_dict()

    async def delete_job(self, job_id: str) -> bool:
        deleted = await self._store.delete_job(job_id)
        if deleted:
            await self._scheduler.reload()
        return deleted

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any]:
        job = await self._store.update_job(job_id, {"enabled": bool(enabled)})
        await self._scheduler.reload()
        return job.to_dict()

    async def preview_job(self, job_id: str, count: int = 5) -> list[dict[str, Any]]:
        job = await self._store.get_job(job_id)
        if job is None:
            raise KeyError("job not found")
        count = max(1, min(int(count), 50))

        tz = ZoneInfo(job.timezone)
        base = datetime.now(tz=tz)
        out: list[dict[str, Any]] = []
        push_dt = base
        for _ in range(count):
            push_dt = _cron_next_push_dt(job.cron_expr, push_dt)
            wake_dt = push_dt - timedelta(seconds=max(0, int(job.wake_offset_seconds or 0)))
            out.append({"wake_at": wake_dt.isoformat(), "push_at": push_dt.isoformat()})
        return out

    async def run_now(self, job_id: str) -> str:
        run_id = await self._scheduler.trigger_run_now(job_id)
        return run_id

