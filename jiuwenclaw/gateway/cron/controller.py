from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar, List

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from zoneinfo import ZoneInfo

from jiuwenclaw.gateway.cron.scheduler import CronSchedulerService, _cron_next_push_dt
from jiuwenclaw.gateway.cron.store import CronJobStore


class CronController:
    """High-level cron API used by WebChannel handlers. Singleton."""

    _instance: ClassVar[CronController | None] = None

    def __init__(self, *, store: CronJobStore, scheduler: CronSchedulerService) -> None:
        self._store = store
        self._scheduler = scheduler

    @classmethod
    def get_instance(
        cls,
        *,
        store: CronJobStore | None = None,
        scheduler: CronSchedulerService | None = None,
    ) -> CronController:
        """Return the singleton instance.

        On first call, store and scheduler are required to create the instance.
        On subsequent calls, both can be omitted to get the existing instance.

        Args:
            store: Required only on first call.
            scheduler: Required only on first call.

        Returns:
            The singleton CronController.

        Raises:
            RuntimeError: If instance not yet initialized and store/scheduler not provided.
        """
        if cls._instance is not None:
            return cls._instance
        if store is None or scheduler is None:
            raise RuntimeError(
                "CronController not initialized. Call get_instance(store=..., scheduler=...) first."
            )
        cls._instance = cls(store=store, scheduler=scheduler)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton. For testing only."""
        cls._instance = None

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

    async def _create_job_tool(
        self,
        name: str,
        cron_expr: str,
        timezone: str,
        targets: list[dict[str, Any]],
        enabled: bool = True,
        description: str = "",
        wake_offset_seconds: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "name": name,
            "cron_expr": cron_expr,
            "timezone": timezone,
            "targets": targets,
            "enabled": enabled,
            "description": description,
        }
        if wake_offset_seconds is not None:
            params["wake_offset_seconds"] = wake_offset_seconds
        return await self.create_job(params)

    async def _update_job_tool(
        self, job_id: str, patch: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.update_job(job_id, patch)

    async def _preview_job_tool(
        self, job_id: str, count: int = 5
    ) -> list[dict[str, Any]]:
        return await self.preview_job(job_id, count)

    def get_tools(self) -> List[Tool]:
        """Return cron job tools for registration in the openJiuwen Runner.
        Tools to be returned:
            list_jobs
            get_job
            create_job
            update_job
            delete_job
            toggle_job
            preview_job

        Usage:
            toolkit = CronController(xxxxxx)
            tools = toolkit.get_tools()
            Runner.resource_mgr.add_tool(tools)
            for t in tools:
                agent.ability_manager.add(t.card)

        Returns:
            List of Tool instances (LocalFunction) ready for Runner/agent registration.
        """

        def make_tool(
            name: str,
            description: str,
            input_params: dict,
            func,
        ) -> Tool:
            card = ToolCard(
                id=f"cron_{name}",
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            make_tool(
                name="cron_list_jobs",
                description="List all cron jobs. Returns a list of job objects with id, name, cron_expr, timezone, enabled, etc.",
                input_params={"type": "object", "properties": {}},
                func=self.list_jobs,
            ),
            make_tool(
                name="cron_get_job",
                description="Get a single cron job by id. Returns job details or None if not found.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "The job id to look up",
                        }
                    },
                    "required": ["job_id"],
                },
                func=self.get_job,
            ),
            make_tool(
                name="cron_create_job",
                description=(
                    "Create a new cron job. Requires name, cron_expr (e.g. '0 9 * * 1-5'), "
                    "timezone (e.g. 'Asia/Shanghai'), and targets (list of {channel_id, session_id?}). "
                    "Optional: enabled, description, wake_offset_seconds."
                ),
                input_params={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Job name"},
                        "cron_expr": {
                            "type": "string",
                            "description": "Cron expression, e.g. '0 9 * * 1-5' for 9am weekdays",
                        },
                        "timezone": {
                            "type": "string",
                            "description": "Timezone, e.g. 'Asia/Shanghai'",
                        },
                        "targets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "channel_id": {"type": "string"},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["channel_id"],
                            },
                            "description": "List of push targets",
                        },
                        "enabled": {
                            "type": "boolean",
                            "description": "Whether job is enabled",
                            "default": True,
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional job description",
                            "default": "",
                        },
                        "wake_offset_seconds": {
                            "type": "integer",
                            "description": "Seconds before push time to wake (default 300)",
                        },
                    },
                    "required": ["name", "cron_expr", "timezone", "targets"],
                },
                func=self._create_job_tool,
            ),
            make_tool(
                name="cron_update_job",
                description="Update an existing cron job. Pass job_id and a patch dict with fields to update (name, enabled, cron_expr, timezone, description, wake_offset_seconds, targets).",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to update"},
                        "patch": {
                            "type": "object",
                            "description": "Fields to update (name, enabled, cron_expr, timezone, description, wake_offset_seconds, targets)",
                        },
                    },
                    "required": ["job_id", "patch"],
                },
                func=self._update_job_tool,
            ),
            make_tool(
                name="cron_delete_job",
                description="Delete a cron job by id. Returns True if deleted, False if not found.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id to delete"},
                    },
                    "required": ["job_id"],
                },
                func=self.delete_job,
            ),
            make_tool(
                name="cron_toggle_job",
                description="Enable or disable a cron job. Pass job_id and enabled (true/false).",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id"},
                        "enabled": {
                            "type": "boolean",
                            "description": "Whether to enable the job",
                        },
                    },
                    "required": ["job_id", "enabled"],
                },
                func=self.toggle_job,
            ),
            make_tool(
                name="cron_preview_job",
                description="Preview next N scheduled run times for a job. Returns list of {wake_at, push_at} timestamps.",
                input_params={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string", "description": "Job id"},
                        "count": {
                            "type": "integer",
                            "description": "Number of runs to preview (1-50, default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["job_id"],
                },
                func=self._preview_job_tool,
            ),
        ]