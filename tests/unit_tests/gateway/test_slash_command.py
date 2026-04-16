# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""jiuwenclaw.gateway.slash_command 单元测试."""

import pytest

from jiuwenclaw.gateway.slash_command import (
    CONTROL_MESSAGE_TEXTS,
    FIRST_BATCH_REGISTRY,
    ParsedControlAction,
    VALID_MODE_LINES,
    format_skills_list_for_notice,
    is_control_like_for_im_batching,
    parse_channel_control_text,
)


@pytest.mark.parametrize(
    ("text", "action", "mode_sub"),
    [
        ("", ParsedControlAction.NONE, None),
        ("hello", ParsedControlAction.NONE, None),
        ("/new_session", ParsedControlAction.NEW_SESSION_OK, None),
        ("/new_session x", ParsedControlAction.NEW_SESSION_BAD, None),
        ("/mode plan", ParsedControlAction.MODE_OK, "plan"),
        ("/mode agent", ParsedControlAction.MODE_OK, "agent"),
        ("/mode fast", ParsedControlAction.MODE_OK, "fast"),
        ("/mode team", ParsedControlAction.MODE_OK, "team"),
        ("/mode claw", ParsedControlAction.MODE_BAD, None),
        ("/mode", ParsedControlAction.MODE_BAD, None),
        ("/skills", ParsedControlAction.SKILLS_OK, None),
        ("/skills extra", ParsedControlAction.NONE, None),
        ("line1\nline2", ParsedControlAction.NONE, None),
    ],
)
def test_parse_channel_control_text(
    text: str,
    action: ParsedControlAction,
    mode_sub: str | None,
) -> None:
    p = parse_channel_control_text(text)
    assert p.action is action
    assert p.mode_subcommand == mode_sub


def test_control_message_texts_contains_mode_variants_and_skills() -> None:
    assert "/new_session" in CONTROL_MESSAGE_TEXTS
    assert "/skills" in CONTROL_MESSAGE_TEXTS
    assert VALID_MODE_LINES <= CONTROL_MESSAGE_TEXTS
    assert "/mode team" in CONTROL_MESSAGE_TEXTS
    assert "/mode fast" in CONTROL_MESSAGE_TEXTS


def test_is_control_like_for_im_batching() -> None:
    assert is_control_like_for_im_batching("/new_session")
    assert is_control_like_for_im_batching("/mode plan")
    assert is_control_like_for_im_batching("/mode foo")
    assert is_control_like_for_im_batching("/new_sessionoops")
    assert is_control_like_for_im_batching("/skills")
    assert not is_control_like_for_im_batching("/skills extra")
    assert not is_control_like_for_im_batching("")
    assert not is_control_like_for_im_batching("a\nb")


def test_format_skills_list_for_notice() -> None:
    out = format_skills_list_for_notice(
        {
            "skills": [
                {"name": "a", "description": "d1", "source": "local"},
                {"name": "b"},
            ]
        }
    )
    assert "【技能列表】" in out
    assert "a" in out
    assert "b" in out


def test_first_batch_registry_ids() -> None:
    ids = {e.id for e in FIRST_BATCH_REGISTRY}
    assert ids == {"new_session", "mode", "skills", "resume"}
