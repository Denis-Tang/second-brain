"""Command line entry point; MCP owns stdout while serving."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shared-brain", description="Shared Brain 本地共享记忆")
    parser.add_argument("--version", action="version", version=json.dumps({"version": __version__}))
    parser.add_argument("--home", type=Path, help="应用配置目录")
    commands = parser.add_subparsers(dest="command")
    for name, description in (
        ("desktop", "打开桌面界面（默认）"),
        ("mcp", "启动 stdio MCP 服务"),
        ("init", "保存 Obsidian Vault 路径，目录由 Agent 创建"),
        ("maintain", "执行一次待处理记忆维护"),
        ("config-mcp", "输出 MCP 客户端连接配置"),
        ("hook", "读取宿主适配后的单个 JSON Hook 事件"),
    ):
        command = commands.add_parser(name, help=description)
        command.add_argument("--home", type=Path, default=argparse.SUPPRESS, help="应用配置目录")
        if name == "init":
            command.add_argument("path", help="Vault 文件夹路径")
    args = parser.parse_args(argv)
    try:
        if args.command == "mcp":
            from .mcp_server import run

            run(home=args.home)
            return 0
        if args.command in (None, "desktop"):
            from .desktop import run

            run(home=args.home)
            result = {"message": "桌面已退出"}
        else:
            from .service import BrainService

            service = BrainService(args.home)
            if args.command == "init":
                result = service.initialize(args.path)
            elif args.command == "hook":
                result = service.hook(json.load(sys.stdin))
            elif args.command == "maintain":
                result = service.maintain()
            else:
                result = service.mcp_config()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        output = sys.stderr if args.command == "mcp" else sys.stdout
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=output)
        return 1
