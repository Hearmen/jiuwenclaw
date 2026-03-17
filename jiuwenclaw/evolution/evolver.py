# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillEvolver - LLM-based experience generation with deduplication."""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from jiuwenclaw.evolution.schema import (
    EvolutionChange,
    EvolutionEntry,
    EvolutionSignal,
    VALID_SECTIONS,
)
from jiuwenclaw.utils import logger

_GENERATE_PROMPT = """\
你是一个 Skill 优化专家。
根据以下 Skill 内容和对话信号，生成一条演进记录。

## 当前 Skill 内容（摘要）
{skill_content}

## 演进信号
{signals_json}

## 对话片段（作为上下文）
{conversation_snippet}

## 已有演进经验（已存在的记录）
{existing_entries_summary}

## 要求
1. 语言必须一致（强制）：输出语言必须与 Skill 完全一致。若 Skill 是中文，输出中文；若 Skill 是英文，输出英文。禁止自行决定语言！
2. 标题层级：使用与 Skill 相同的标题层级（##、### 等）
3. 记录格式（强制）：只生成 1 条记录！禁止 2 条或更多！输出 JSON 的 content 字段只能有 1 个标题 + 2-3 个分点！
4. 聚焦信号：优先选择与当前任务直接相关的信号，忽略无关噪音
5. 提取通用规则：生成可复用的规则，非临时补丁
   - 好："遇到 X 类型错误时，先检查 Y 是否正确再执行 Z"
   - 差："某用户某次提到某问题"
6. 专注单一类型：一次只生成一个 section 类型的改进（Instructions/Examples/Troubleshooting 之一），不混合多类型
7. 分点格式：只使用无序列表（- 或 *），禁止层级（不能有子分点）
8. 精炼语言：内容简洁，避免冗余描述
9. 高质量增量：生成的演进内容必须是 Skill 中未提及的新知识，能指导后续使用并提升 Agent 执行效率。
10. 相关性判断（强制）：判断当前发现的问题是否与 Skill 本身相关
    - 相关：问题由 Skill 的指令、脚本、示例或排查逻辑导致
    - 不相关：问题由外部因素导致（如网络、环境、权限、第三方服务等）
11. 去重判断（强制）：对比「已有演进经验」
    - 相同：新生成的内容与已有记录实质相同 -> 输出 {{"action": "skip"}}
    - 相似：新内容与某条已有记录高度相关但有增量信息 -> 输出合并后的完整内容，并标注 "merge_target": "<目标记录 id>"
    - 全新：与已有记录无关 -> 正常输出

只输出以下 JSON，不要其他内容：
{{
  "section": "Instructions | Examples | Troubleshooting",
  "action": "append | skip",
  "relevant": true | false,
  "content": "Markdown 内容，1 个标题 + 2-3 个分点，无层级（只生成 1 条记录，禁止重复内容）",
  "merge_target": "ev_xxxxxxxx 或 null"
}}"""


def build_conversation_snippet(
    messages: List[dict],
    max_messages: int = 30,
    content_preview_chars: int = 300,
) -> str:
    """Build a compact conversation snippet for LLM context."""
    if not messages:
        return ""

    def _extract_text(m: dict) -> str:
        content = m.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    lines: List[str] = []
    for msg in messages[-max_messages:]:
        role = msg.get("role", "unknown")
        text = _extract_text(msg).strip() or "(无文本)"
        if len(text) > content_preview_chars:
            text = text[:content_preview_chars] + "..."

        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            names = [tc.get("name", "") for tc in tool_calls if isinstance(tc, dict)]
            prefix = f"[assistant] (tool_calls: {', '.join(names)})\n  "
        else:
            prefix = f"[{role}] "

        lines.append(prefix + text)
    return "\n".join(lines)


class SkillEvolver:
    """Pure logic layer: LLM-based experience generation with history dedup.

    Does NOT perform any file IO. All data is passed in as arguments and
    returned as values.
    """

    def __init__(self, llm: Any, model: str) -> None:
        self._llm = llm
        self._model = model

    async def generate_skill_experience(
        self,
        skill_name: str,
        signals: List[EvolutionSignal],
        skill_content: str,
        conversation_snippet: str,
        existing_entries: List[EvolutionEntry],
    ) -> Optional[EvolutionEntry]:
        """Generate an evolution entry via LLM, with history dedup.

        Args:
            skill_name: Target skill name.
            signals: Detected signals attributed to this skill.
            skill_content: Current SKILL.md content.
            conversation_snippet: Recent conversation summary.
            existing_entries: Already-pending entries for dedup comparison.

        Returns:
            A new EvolutionEntry, or None if LLM decides to skip.
        """
        if not signals:
            return None

        signals_json = json.dumps(
            [s.to_dict() for s in signals], ensure_ascii=False, indent=2
        )

        existing_summary = self._build_existing_summary(existing_entries)

        prompt = _GENERATE_PROMPT.format(
            skill_content=skill_content[:2000],
            signals_json=signals_json,
            conversation_snippet=(conversation_snippet or "").strip(),
            existing_entries_summary=existing_summary or "(无已有记录)",
        )

        logger.info("[SkillEvolver] calling LLM (skill=%s)", skill_name)
        try:
            response = await self._llm.invoke(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("[SkillEvolver] LLM call failed: %s", exc)
            return None

        change = self._parse_llm_response(raw)
        if change is None:
            return None
        if change.action == "skip":
            logger.info("[SkillEvolver] LLM decided to skip (dedup)")
            return None
        if not change.content.strip() or not change.relevant:
            logger.info("[SkillEvolver] LLM thinks no changes needed")
            return None

        source = signals[0].type if signals else "unknown"
        context = "; ".join(s.excerpt for s in signals)
        entry = EvolutionEntry.make(source=source, context=context, change=change)
        logger.info(
            "[SkillEvolver] generated entry %s -> [%s] merge_target=%s",
            entry.id,
            change.section,
            change.merge_target,
        )
        return entry

    def update_llm(self, llm: Any, model: str) -> None:
        self._llm = llm
        self._model = model

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_existing_summary(entries: List[EvolutionEntry]) -> str:
        if not entries:
            return ""
        lines: List[str] = []
        for e in entries:
            lines.append(f"- [{e.id}] [{e.change.section}] {e.change.content}")
        return "\n".join(lines)

    @staticmethod
    def _parse_llm_response(raw: str) -> Optional[EvolutionChange]:
        """从 LLM 返回里解析出 EvolutionChange。"""
        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw, flags=re.MULTILINE)
        raw = raw.strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if not m:
                logger.warning(
                    "[SkillEvolver] cannot parse LLM response as JSON: %s",
                    raw[:200],
                )
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                logger.warning("[SkillEvolver] JSON parse failed")
                return None

        action = data.get("action", "append")
        if action == "skip":
            return EvolutionChange(section="", action="skip", content="", relevant=False)

        section = data.get("section", "Troubleshooting")
        if section not in VALID_SECTIONS:
            section = "Troubleshooting"

        merge_target = data.get("merge_target")
        if merge_target == "null" or merge_target is None:
            merge_target = None

        return EvolutionChange(
            section=section,
            action="append",
            content=data.get("content", ""),
            relevant=data.get("relevant", True),
            merge_target=merge_target,
        )
