# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from openjiuwen.core.foundation.tool import tool

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Content-Type": "application/json",
}


@dataclass(frozen=True)
class VideoUnderstandingRequest:
    query: str
    video_path: str
    model: str = "glm-4.6v"
    timeout_seconds: int = 120
    max_tokens: int = 2048
    temperature: float = 0.2
    thinking_enabled: bool = False


def _http_post(url: str, **kwargs) -> requests.Response:
    """Try normal request first; retry without env proxies on ProxyError."""
    try:
        return requests.post(url, **kwargs)
    except requests.exceptions.ProxyError:
        with requests.Session() as session:
            session.trust_env = False
            return session.post(url, **kwargs)


def _guess_video_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("video/"):
        return mime

    ext = Path(path).suffix.lower()
    mapping = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".mpeg": "video/mpeg",
        ".mpg": "video/mpeg",
        ".m4v": "video/x-m4v",
    }
    return mapping.get(ext, "video/mp4")


def _video_path_to_url(video_path: str) -> str:
    """Convert a local video path to data URI, or keep remote URL as-is."""
    value = (video_path or "").strip()
    if not value:
        raise ValueError("video_path cannot be empty")

    if value.startswith(("http://", "https://")):
        return value

    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"video file does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"video_path is not a file: {path}")

    mime = _guess_video_mime(str(path))
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    # Best-effort local-path support. If the API side rejects data URI for video,
    # upload the file to an accessible HTTP(S) URL and pass that URL instead.
    return f"data:{mime};base64,{encoded}"


def _extract_answer(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    first = choices[0]
    if not isinstance(first, dict):
        return ""

    message = first.get("message", {})
    if not isinstance(message, dict):
        return ""

    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()

    # Compatible fallback for structured content arrays.
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    texts.append(str(text))
        return "\n".join(texts).strip()

    return str(content).strip()


def _normalize_request(inputs: dict[str, Any]) -> VideoUnderstandingRequest:
    query = str(inputs.get("query", "") or "").strip()
    video_path = str(inputs.get("video_path", "") or "").strip()
    model = str(inputs.get("model", "glm-4.6v") or "glm-4.6v").strip()

    timeout_seconds = int(inputs.get("timeout_seconds", 120))
    max_tokens = int(inputs.get("max_tokens", 2048))
    temperature = float(inputs.get("temperature", 0.2))
    thinking_enabled = bool(inputs.get("thinking_enabled", False))

    if not query:
        raise ValueError("query cannot be empty.")
    if not video_path:
        raise ValueError("video_path cannot be empty.")

    timeout_seconds = max(10, min(timeout_seconds, 600))
    max_tokens = max(128, min(max_tokens, 8192))
    temperature = max(0.0, min(temperature, 2.0))

    return VideoUnderstandingRequest(
        query=query,
        video_path=video_path,
        model=model,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
        thinking_enabled=thinking_enabled,
    )


def _glm_video_understanding_sync(req: VideoUnderstandingRequest) -> str:
    api_key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ZHIPU_API_KEY is not set")

    api_url = os.environ.get(
        "ZHIPU_API_URL",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    ).strip()

    video_url = _video_path_to_url(req.video_path)

    payload: dict[str, Any] = {
        "model": req.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "video_url",
                        "video_url": {
                            "url": video_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": req.query,
                    },
                ],
            }
        ],
        "stream": False,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }

    if req.thinking_enabled:
        payload["thinking"] = {"type": "enabled"}

    headers = {
        **_REQUEST_HEADERS,
        "Authorization": f"Bearer {api_key}",
    }

    response = _http_post(
        api_url,
        headers=headers,
        json=payload,
        timeout=req.timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()

    answer = _extract_answer(data)
    if not answer:
        return "[ERROR]: GLM returned empty answer."

    return answer


@tool(
    name="video_understanding",
    description=(
        "Use GLM-4.6V to understand a video and answer a user query. "
        "Input query and video_path. HTTP/HTTPS video URL is preferred; "
        "local file path is supported on a best-effort basis."
    ),
)
async def video_understanding(inputs: dict[str, Any], **kwargs) -> str:
    _ = kwargs
    try:
        req = _normalize_request(inputs or {})
        return await asyncio.to_thread(_glm_video_understanding_sync, req)
    except Exception as exc:
        return f"[ERROR]: glm video understanding failed: {exc}"