"""Minimal synchronous MCP test client; exercises real pipes and legacy negotiation."""
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path


class StdioClient:
    def __init__(self):
        self.responses = queue.Queue()
        self.stderr = []
        self.next_id = 0

    def __enter__(self):
        root = Path(__file__).resolve().parents[1]
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(root / "mcp_server.py")], cwd=root.parent,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=os.environ | {"PYTHONUTF8": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        def read():
            for line in self.process.stdout:
                self.responses.put(line)
            self.responses.put(None)
        def errors():
            self.stderr.extend(self.process.stderr)
        self.reader = threading.Thread(target=read, daemon=True)
        self.errors = threading.Thread(target=errors, daemon=True)
        self.reader.start()
        self.errors.start()
        try:
            self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                       "clientInfo": {"name": "shorts-test", "version": "1"}})
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            line = self.responses.get(timeout=30)
            if line is None:
                raise RuntimeError("MCP server exited: " + "".join(self.stderr))
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def __exit__(self, *_):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self.errors.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()
