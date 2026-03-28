# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Xiaoyi Handset Tools - 小艺手机端设备工具.

该目录包含需要连接小艺手机端设备才能使用的工具。
这些工具通过 WebSocket 与手机端通信，调用设备原生能力。

工具分类：
- 定位: get_user_location
- 备忘录: create_note, search_notes, modify_note
- 日历: create_calendar_event, search_calendar_event
- 联系人: search_contact
- 相册: search_photo_gallery, upload_photo
- 文件: search_file, upload_file
- 电话: call_phone
- 短信/消息: send_message, search_message
- 闹钟: create_alarm, search_alarms, modify_alarm, delete_alarm
- 收藏: xiaoyi_collection
- 推送记录: view_push_result
- GUI 自动化: xiaoyi_gui_agent
- 图像理解: image_reading
- 本地上传公网 URL: upload_file_and_get_url
"""

from .location_tool import get_user_location
from .note_tools import create_note, search_notes, modify_note
from .calendar_tools import create_calendar_event, search_calendar_event
from .contact_tools import search_contact
from .photo_tools import search_photo_gallery, upload_photo
from .file_tools import search_file, upload_file
from .phone_tools import call_phone
from .message_tools import send_message, search_message
from .alarm_tools import create_alarm, search_alarms, modify_alarm, delete_alarm
from .xiaoyi_collection_tool import xiaoyi_collection
from .xiaoyi_gui_tool import xiaoyi_gui_agent
from .image_reading_tool import image_reading

__all__ = [
    "get_user_location",
    "create_note",
    "search_notes",
    "modify_note",
    "create_calendar_event",
    "search_calendar_event",
    "search_contact",
    "search_photo_gallery",
    "upload_photo",
    "search_file",
    "upload_file",
    "call_phone",
    "send_message",
    "search_message",
    "create_alarm",
    "search_alarms",
    "modify_alarm",
    "delete_alarm",
    "xiaoyi_collection",
    "xiaoyi_gui_agent",
    "image_reading",
]

