#!/usr/bin/env python3
"""Orchestra Demo Agent — 零依赖 MCP mock，开箱即用。

启动后模拟一个真实 Agent：接受 /execute 请求，返回模拟结果。
让新用户在没有任何外部 Agent 的情况下体验完整流水线。

用法:
    python scripts/demo_agent.py developer 18961
    python scripts/demo_agent.py tester    18962

端点:
    GET  /health        → 200 ok
    GET  /capabilities  → role + capabilities + tools
    POST /execute       → 接收任务，返回模拟输出
    POST /cancel        → 取消任务（no-op）
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler


def main() -> None:
    if len(sys.argv) < 3:
        print("用法: demo_agent.py <role> <port>", file=sys.stderr)
        print("  role: developer | tester | designer | ci_engineer | chat | standby", file=sys.stderr)
        print("  port: HTTP 监听端口 (如 18961)", file=sys.stderr)
        sys.exit(1)

    role = sys.argv[1]
    port = int(sys.argv[2])

    caps = _capabilities_for(role)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # 静默日志
            pass

        def _json(self, code: int, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/health", "/ping"):
                self._json(200, {"status": "healthy", "role": role})
            elif self.path == "/capabilities":
                self._json(200, {
                    "role": role,
                    "capabilities": caps,
                    "tools": [f"mock_{role}_{t}" for t in ["read", "write", "run"]],
                    "model": "demo-model",
                    "version": "0.1.0",
                })
            elif self.path == "/metrics":
                self._json(200, {
                    "busy_slots": 0,
                    "queue_depth": 0,
                    "tokens_consumed_total": 0,
                })
            else:
                self._json(200, {"status": "ok", "role": role})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}

            if self.path == "/execute":
                task_input = body.get("input", "")
                stage = body.get("stage", "unknown")
                output_schema = body.get("output_schema")

                # 根据 stage 名生成合理的 mock 输出
                output = _mock_output(stage, task_input, role, output_schema)

                self._json(200, {
                    "output": output,
                    "tokens_consumed": len(str(task_input)) // 4 + 50,
                    "cost_usd": 0.001,
                    "duration_seconds": 0.05,
                })
            elif self.path == "/cancel":
                self._json(200, {"cancelled": True})
            else:
                self._json(404, {"error": "not found"})

    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"[DemoAgent] role={role} port={port} caps={caps}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def _capabilities_for(role: str) -> list[str]:
    return {
        "developer":   ["python", "git", "shell"],
        "tester":      ["python", "pytest", "coverage"],
        "designer":    ["ui-design", "asset-export"],
        "ci_engineer": ["docker", "deploy", "ci"],
        "chat":        ["chat", "summarize", "analyze"],
        "standby":     ["generic", "fallback"],
    }.get(role, ["generic"])


def _mock_output(stage: str, task_input: object, role: str, schema: dict | None) -> object:
    """根据 stage 名生成有意义（而非固定）的模拟输出。"""
    task_str = str(task_input) if isinstance(task_input, str) else json.dumps(task_input, ensure_ascii=False)

    if "design" in stage or "review" in stage:
        return {
            "task": task_str,
            "has_ui_change": True,
            "ui_spec": "需要小鸟精灵、水管精灵、背景图、计分 UI",
        }
    if "code" in stage:
        return {
            "patch": (
                "# Flappy Bird — 由 Orchestra Demo Agent 生成\n"
                "extends Node2D\n"
                "var velocity = Vector2(0, 0)\n"
                "const GRAVITY = 500\n"
                "const FLAP = -250\n"
                "func _physics_process(delta):\n"
                "    velocity.y += GRAVITY * delta\n"
                "    if Input.is_action_just_pressed('flap'):\n"
                "        velocity.y = FLAP\n"
                "    position += velocity * delta\n"
            ),
            "files_changed": ["bird.gd"],
        }
    if "test" in stage or "verify" in stage:
        return {"result": "pass", "coverage": 92.5}
    if "diagnose" in stage:
        return {
            "bugs": [
                {"id": "BUG-01", "description": "水管碰撞检测偏移 2px",
                 "file": "pipe.gd", "severity": "minor"},
                {"id": "BUG-02", "description": "计分在 Game Over 后仍递增",
                 "file": "score.gd", "severity": "major"},
            ]
        }
    if "fix" in stage or "bug" in stage:
        return {"fix": f"已修复: {task_str[:80]}", "patch": "diff --git ..."}
    if "art" in stage or "asset" in stage or "sprite" in stage:
        return {"assets": ["bird.png", "pipe.png", "bg.png", "score_font.tres"]}
    if "deploy" in stage:
        return {"deployed": True, "url": f"https://demo-{uuid.uuid4().hex[:8]}.example.com"}
    if "ui" in stage or "screenshot" in stage:
        return {"result": "pass", "screenshots": ["home.png", "gameplay.png", "gameover.png"]}

    # 通用回退
    return {"result": f"[DemoAgent:{role}] 完成: {task_str[:100]}"}


if __name__ == "__main__":
    main()
