#!/usr/local/lib/hermes-agent/venv/bin/python3
"""
MCP REST Bridge — 在 Hermes MCP (FastMCP) 前面挂一层 REST API。
Orchestra Worker 通过 POST /execute 调用此桥接，桥接内部调 mcp-hermes.sh。

用法: mcp_rest_bridge.py walnut 18961
"""

import json
import sys
import os
import subprocess
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler

PROFILE = sys.argv[1]
PORT = int(sys.argv[2])

MCP_HERMES = "/opt/hermes-pipeline/mcp-hermes.sh"

def call_hermes(tool: str, args_dict: dict) -> dict:
    """同步调用 mcp-hermes.sh，返回 JSON 结果。"""
    # mcporter 期望 --args 传 JSON
    proc = subprocess.run(
        ["bash", MCP_HERMES, PROFILE, tool, "--args", json.dumps(args_dict)],
        capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        return {"error": f"mcp-hermes failed: {proc.stderr[:200]}"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"output": proc.stdout}

class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默

    def do_GET(self):
        if self.path in ("/health", "/ping"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"MCP REST Bridge")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

    def do_POST(self):
        if self.path == "/execute":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            task = json.loads(body)
            
            # 构造消息：把 task.input 作为 agent 的 user prompt
            stage = task.get("stage", "unknown")
            task_input = task.get("input", "")
            
            # 调用 hermes messages_send 发送任务给 agent
            msg_data = {
                "target": task.get("target", "qqbot"),
                "message": f"[Orchestra Task: {stage}]\n\n{task_input}",
            }
            
            try:
                result = call_hermes("messages_send", msg_data)
                # 用 conversations_list 等 agent 回复...
                # 简化返回：直接返回发送结果
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "output": result,
                    "tokens_consumed": 0,
                    "cost_usd": 0.0,
                }).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), BridgeHandler)
    print(f"[Bridge] {PROFILE} :{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
