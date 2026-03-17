# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""EvolutionService - Unified facade for the skill evolution system."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from jiuwenclaw.evolution.evolver import SkillEvolver, build_conversation_snippet
from jiuwenclaw.evolution.schema import EvolutionSignal, EvolutionType
from jiuwenclaw.evolution.signal_detector import SignalDetector
from jiuwenclaw.evolution.store import EvolutionStore
from jiuwenclaw.utils import logger

_APPROVAL_TIMEOUT = 300  # seconds


class EvolutionService:
    """Unified facade for the skill online evolution system.

    Owns SignalDetector, SkillEvolver, and EvolutionStore.
    Handles the complete lifecycle: detect -> truncate -> route -> generate
    -> approve -> persist.
    """

    def __init__(
        self,
        llm: Any,
        model: str,
        skills_base_dir: str,
        auto_scan: bool = False,
    ) -> None:
        self._store = EvolutionStore(skills_base_dir)
        self._evolver = SkillEvolver(llm, model)
        self._auto_scan = auto_scan
        self._pending_approvals: Dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def auto_scan(self) -> bool:
        return self._auto_scan

    @auto_scan.setter
    def auto_scan(self, value: bool) -> None:
        self._auto_scan = value

    @property
    def skills_base_dir(self) -> str:
        return str(self._store.base_dir)

    @property
    def store(self) -> EvolutionStore:
        return self._store

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def update_llm(self, llm: Any, model: str) -> None:
        self._evolver.update_llm(llm, model)

    # ------------------------------------------------------------------
    # Manual trigger: /evolve command
    # ------------------------------------------------------------------

    async def handle_evolve_command(
        self,
        query: str,
        session: Any,
        messages: List[Any],
    ) -> Dict[str, Any]:
        """/evolve [list | <skill_name>] command handler.

        Args:
            query: Raw user input starting with /evolve.
            session: Session for streaming / approval.
            messages: Raw history messages (BaseMessage or dict); parsed internally.
        """
        skill_names = self._store.list_skill_names()

        parts = query.split(maxsplit=1)
        skill_arg = parts[1].strip() if len(parts) > 1 else ""

        if not skill_arg or skill_arg == "list":
            if not skill_names:
                return {
                    "output": "当前 skills_base_dir 下未找到任何 Skill 目录。",
                    "result_type": "answer",
                }
            summary = self._store.list_pending_summary(skill_names)
            return {
                "output": f"**Skills 演进记录：**\n\n{summary}",
                "result_type": "answer",
            }

        skill_name = skill_arg
        if skill_name not in skill_names:
            available = "、".join(skill_names) or "（无可用 Skill）"
            return {
                "output": (
                    f"在 skills_base_dir 下未找到 Skill '{skill_name}'。\n"
                    f"当前可用 Skill：{available}\n"
                    f"可使用 /evolve list 查看所有记录。"
                ),
                "result_type": "error",
            }

        parsed = self._parse_messages(messages)
        signals = self._detect_signals(parsed, skill_names)
        if not signals:
            return {
                "output": "当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n",
                "result_type": "answer",
            }
        attributed = [s for s in signals if s.skill_name == skill_name]
        entry = await self._generate_experience_for_skill(skill_name, attributed, parsed)
        if entry is None:
            return {
                "output": "当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n",
                "result_type": "answer",
            }

        if session is not None:
            keep = await self._request_approval(session, skill_name, entry)
            if keep:
                self._store.append_entry(skill_name, entry)
                return {
                    "output": (
                        f"已记录演进经验到 Skill '{skill_name}'：\n"
                        f"  **[{entry.change.section}]** {entry.change.content[:200]}\n\n"
                        f"（evolutions.json 已更新，自动生效；"
                        f"可使用 `/solidify {skill_name}` 将经验固化到 SKILL.md 本体）"
                    ),
                    "result_type": "answer",
                }
            return {
                "output": f"已丢弃 Skill '{skill_name}' 的演进内容，evolutions.json 未变更。",
                "result_type": "answer",
            }

        self._store.append_entry(skill_name, entry)
        return {
            "output": (
                f"已记录演进经验到 Skill '{skill_name}'：\n"
                f"  **[{entry.change.section}]** {entry.change.content[:200]}"
            ),
            "result_type": "answer",
        }

    # ------------------------------------------------------------------
    # Auto trigger: after conversation round
    # ------------------------------------------------------------------

    async def run_auto_evolution(
        self,
        session: Any,
        history_messages: List[Any],
    ) -> None:
        """Auto-scan after a conversation round, generate + approve + persist."""
        skill_names = self._store.list_skill_names()
        if not skill_names:
            return

        parsed = self._parse_messages(history_messages)
        signals = self._detect_signals(parsed, skill_names)
        if not signals:
            return

        await self._route_and_process(signals, parsed, session)

    # ------------------------------------------------------------------
    # Solidify command
    # ------------------------------------------------------------------

    def handle_solidify_command(self, query: str) -> Dict[str, Any]:
        """/solidify <skill_name> handler."""
        parts = query.split(maxsplit=1)
        skill_name = parts[1].strip() if len(parts) > 1 else ""
        if not skill_name:
            return {
                "output": "请指定 Skill 名称：`/solidify <skill_name>`",
                "result_type": "error",
            }
        count = self._store.solidify(skill_name)
        if count == 0:
            msg = f"Skill '{skill_name}' 没有待固化的演进经验。"
        else:
            msg = f"已将 {count} 条演进经验固化到 Skill '{skill_name}' 的 SKILL.md。"
        return {"output": msg, "result_type": "answer"}

    # ------------------------------------------------------------------
    # Approval flow (migrated from react_agent.py)
    # ------------------------------------------------------------------

    async def _request_approval(
        self,
        session: Any,
        skill_name: str,
        entry: Any,
    ) -> bool:
        """Send approval request to user. Returns True=keep, False=discard."""
        from openjiuwen.core.session.stream import OutputSchema

        request_id = f"evolve_approve_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_approvals[request_id] = future

        content_preview = entry.change.content[:1000]
        try:
            await session.write_stream(
                OutputSchema(
                    type="chat.ask_user_question",
                    index=0,
                    payload={
                        "request_id": request_id,
                        "questions": [
                            {
                                "question": (
                                    f"**Skill '{skill_name}' 演进生成了新内容：**\n\n"
                                    f"{content_preview}"
                                ),
                                "header": "演进审批",
                                "options": [
                                    {"label": "接收", "description": "保留此演进经验"},
                                    {"label": "拒绝", "description": "丢弃此演进经验"},
                                ],
                                "multi_select": False,
                            }
                        ],
                    },
                )
            )
        except Exception:
            logger.debug("[EvolutionService] approval popup send failed", exc_info=True)
            self._pending_approvals.pop(request_id, None)
            return True

        try:
            return await asyncio.wait_for(future, timeout=_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.info(
                "[EvolutionService] approval timeout (skill=%s), auto-keeping",
                skill_name,
            )
            return True
        finally:
            self._pending_approvals.pop(request_id, None)

    def resolve_approval(self, request_id: str, answers: list) -> bool:
        """Resolve a pending approval future with user's answer.

        Called by interface.py on chat.user_answer.
        Returns True if resolved, False if not found.
        """
        future = self._pending_approvals.get(request_id)
        if future is None or future.done():
            return False

        keep = (
            "接收" in answers[0].get("selected_options", [])
            if answers and isinstance(answers[0], dict)
            else False
        )
        future.set_result(keep)
        logger.info(
            "[EvolutionService] approval resolved: request_id=%s keep=%s",
            request_id,
            keep,
        )
        return True

    # ------------------------------------------------------------------
    # Core internal: detect -> truncate -> route
    # ------------------------------------------------------------------

    def _detect_signals(
        self,
        parsed_messages: List[dict],
        skill_names: List[str],
    ) -> List[EvolutionSignal]:
        """Call SignalDetector + truncate to last signal.

        Args:
            parsed_messages: Already-normalized message dicts (call _parse_messages upstream).
            skill_names: Known skill directory names.
        """
        skill_dir_map = {
            name: str(self._store.base_dir / name / "SKILL.md")
            for name in skill_names
            if self._store.skill_exists(name)
        }
        detector = SignalDetector(skill_dir_map=skill_dir_map)
        signals = detector.detect(parsed_messages)

        if len(signals) > 1:
            signals = signals[-1:]
            logger.info("[EvolutionService] truncated to last signal")

        if signals:
            logger.info(
                "[EvolutionService] detected %d signal(s): %s",
                len(signals),
                json.dumps([s.to_dict() for s in signals], ensure_ascii=False),
            )
        return signals

    async def _route_and_process(
        self,
        signals: List[EvolutionSignal],
        parsed_messages: List[dict],
        session: Any,
    ) -> None:
        """Route signals to the appropriate handler.

        Currently only SKILL_EXPERIENCE is implemented. Signals are already
        truncated to at most one by _detect_signals, so we process directly
        without grouping.
        """
        for sig in signals:
            if sig.evolution_type == EvolutionType.SKILL_EXPERIENCE and sig.skill_name:
                await self._evolve_skill_experience(sig, signals, parsed_messages, session)
            # Future: elif sig.evolution_type == EvolutionType.NEW_SKILL:
            #             await self._evolve_new_skill(...)

    async def _evolve_skill_experience(
        self,
        signal: EvolutionSignal,
        all_signals: List[EvolutionSignal],
        parsed_messages: List[dict],
        session: Any,
    ) -> None:
        """Evolve an existing skill: generate experience -> approve -> persist."""
        skill_name = signal.skill_name
        attributed = [s for s in all_signals if s.skill_name == skill_name]
        entry = await self._generate_experience_for_skill(skill_name, attributed, parsed_messages)
        if entry is None:
            return
        try:
            keep = await self._request_approval(session, skill_name, entry)
            if keep:
                self._store.append_entry(skill_name, entry)
                logger.info("[EvolutionService] kept: skill=%s id=%s", skill_name, entry.id)
            else:
                logger.info("[EvolutionService] discarded: skill=%s id=%s", skill_name, entry.id)
        except Exception as exc:
            logger.warning(
                "[EvolutionService] approval flow error (skill=%s): %s",
                skill_name,
                exc,
            )

    async def _generate_experience_for_skill(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        messages: List[dict],
    ) -> Any:
        """Generate an evolution entry for a single skill."""
        skill_content = self._store.read_skill_content(skill_name)
        existing = self._store.get_pending_entries(skill_name)
        snippet = build_conversation_snippet(messages)
        try:
            return await self._evolver.generate_skill_experience(
                skill_name, signals, skill_content, snippet, existing
            )
        except Exception as exc:
            logger.warning(
                "[EvolutionService] generate failed (skill=%s): %s", skill_name, exc
            )
            return None

    @staticmethod
    def _parse_messages(messages: List[Any]) -> List[dict]:
        """Normalize BaseMessage or dict messages to plain dicts."""
        result: List[dict] = []
        for msg in messages:
            if isinstance(msg, dict):
                result.append(msg)
            elif hasattr(msg, "role"):
                d: dict = {
                    "role": getattr(msg, "role", ""),
                    "content": str(getattr(msg, "content", "") or ""),
                }
                tool_calls = getattr(msg, "tool_calls", None)
                if tool_calls:
                    d["tool_calls"] = [
                        {
                            "id": getattr(tc, "id", ""),
                            "name": getattr(tc, "name", ""),
                            "arguments": getattr(tc, "arguments", ""),
                        }
                        for tc in tool_calls
                    ]
                name = getattr(msg, "name", None)
                if name:
                    d["name"] = name
                result.append(d)
        return result
