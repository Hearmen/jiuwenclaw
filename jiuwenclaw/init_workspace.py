"""CLI for initializing runtime data into ~/.jiuwenclaw.

无论是通过 pip/whl 安装，还是在源码目录里直接运行：
- 运行本脚本会先询问语言偏好（zh/en），写入 config 的 preferred_language；
- 同时复制 config.yaml、builtin_rules.yaml、.env.template、agent 模板等到 ~/.jiuwenclaw；
- 根据语言偏好复制多语言文件（AGENT.md、HEARTBEAT.md、IDENTITY.md、SOUL.md 等），
  源文件使用 _ZH/_EN 后缀，目标文件不带后缀。

使用方式:
- jiuwenclaw-init -f: 强制清理，删除整个 ~/.jiuwenclaw 后重新初始化
- jiuwenclaw-init: 保留原有数据，执行迁移合并
"""

from __future__ import annotations

import argparse
import logging
import sys

from jiuwenclaw.utils import init_user_workspace


def run_init(force: bool = False) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    target = init_user_workspace(overwrite=force)
    if target == "cancelled":
        return 1
    print(f"[jiuwenclaw-init] initialized: {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize jiuwenclaw workspace directory (~/.jiuwenclaw)"
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Force clean initialization: delete entire ~/.jiuwenclaw before init",
    )
    args = parser.parse_args()
    return run_init(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
