# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Collection tool - 小艺收藏工具.

包含：
- xiaoyi_collection: 检索用户在小艺收藏中记下来的公共知识数据
"""

from __future__ import annotations

import json
from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenclaw.utils import logger
from .utils import execute_device_command, raise_if_device_error


@tool(
    name="xiaoyi_collection",
    description=(
        "检索用户在小艺收藏中记下来的公共知识数据，可以给用户提供个性化体验。"
        "当用户语料中涉及从我的小艺收藏或者查看我的收藏或者我xx时候收藏的xxx帮我看一下这种类型的语料的时候需要使用此工具。"
        "注意:操作超时时间为60秒,请勿重复调用此工具,如果超时或失败,最多重试一次。"
    ),
)
async def xiaoyi_collection(query_all: str = "true") -> Dict[str, Any]:
    """检索小艺收藏（与 xy_channel xiaoyi-collection-tool.ts 行为对齐）.

    Args:
        query_all: 是否查询全部收藏，默认 \"true\"

    Returns:
        content[0].text: JSON 字符串（success / memoryInfo 或 totalResults+collections+message）
    """
    try:
        query_all_value = query_all or "true"

        logger.info(
            "[XIAOYI_COLLECTION_TOOL] Starting execution - queryAll=%r",
            query_all_value,
        )

        command = {
            "header": {
                "namespace": "Common",
                "name": "Action",
            },
            "payload": {
                "cardParam": {},
                "executeParam": {
                    "executeMode": "background",
                    "intentName": "QueryCollection",
                    "bundleName": "com.huawei.hmos.vassistant",
                    "needUnlock": True,
                    "actionResponse": True,
                    "appType": "OHOS_APP",
                    "timeOut": 5,
                    "intentParam": {
                        "queryAll": query_all_value,
                    },
                    "permissionId": [],
                    "achieveType": "INTENT",
                },
                "responses": [{"resultCode": "", "displayText": "", "ttsText": ""}],
                "needUploadResult": True,
                "noHalfPage": False,
                "pageControlRelated": False,
            },
        }

        outputs = await execute_device_command("QueryCollection", command)

        if not isinstance(outputs, dict):
            outputs = {"outputs": outputs}

        raise_if_device_error(outputs, "查询小艺收藏失败")

        result = outputs.get("result")

        if result is None:
            logger.warning("[XIAOYI_COLLECTION_TOOL] No collection data found")
            payload = {
                "success": True,
                "memoryInfo": [],
                "message": "未找到收藏数据",
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    }
                ]
            }

        nested = result.get("result") if isinstance(result, dict) else None
        if isinstance(nested, dict):
            memory_info = nested.get("memoryInfo") or []
        else:
            memory_info = []
        if not isinstance(memory_info, list):
            memory_info = []

        logger.info(
            "[XIAOYI_COLLECTION_TOOL] Collections found: %s items",
            len(memory_info),
        )

        simplified_collections = []
        for item in memory_info:
            if not isinstance(item, dict):
                continue
            simplified_collections.append(
                {
                    "uuid": item.get("uuid"),
                    "type": item.get("type"),
                    "status": item.get("status"),
                    "collectionTime": item.get("collectionTime"),
                    "editTime": item.get("editTime"),
                    "title": item.get("linkTitle")
                    or item.get("aiTitle")
                    or item.get("textTitle")
                    or item.get("imageTitle")
                    or item.get("podcastTitle")
                    or "",
                    "description": item.get("description")
                    or item.get("abstract")
                    or "",
                    "content": item.get("textContent") or "",
                    "linkUrl": item.get("linkUrl"),
                    "linkType": item.get("linkType"),
                    "appName": item.get("appNameFromPab")
                    or item.get("appName")
                    or "",
                    "labels": item.get("label") or [],
                    "collectionMethod": item.get("collectionMethod"),
                }
            )

        payload: Dict[str, Any] = {
            "success": True,
            "totalResults": len(simplified_collections),
            "collections": simplified_collections,
        }
        if isinstance(result, dict) and "message" in result:
            payload["message"] = result["message"]

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            ]
        }

    except Exception as e:
        logger.error(f"[XIAOYI_COLLECTION_TOOL] Failed to query collection: {e}")
        raise RuntimeError(f"查询小艺收藏失败: {str(e)}") from e
