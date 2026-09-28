"""Register this source checkout in Claude Desktop without replacing other settings."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

SERVER_NAME = "youtube-shorts"


def server_entry(python: Path) -> dict:
    python = python.resolve()
    if not python.is_file():
        raise ValueError(f"Python interpreter not found: {python}")
    return {"command": str(python),
            "args": ["-u", str(Path(__file__).resolve().with_name("mcp_server.py"))],
            "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}}


def find_config() -> Path:
    if sys.platform == "win32":
        standard = Path(os.environ["APPDATA"]) / "Claude/claude_desktop_config.json"
        packages = Path(os.environ["LOCALAPPDATA"]) / "Packages"
        store = list(packages.glob("Claude_*/LocalCache/Roaming/Claude/claude_desktop_config.json"))
        existing = [path for path in [standard, *store] if path.is_file()]
        if len(existing) > 1:
            raise ValueError("Multiple Claude configs found. Choose one explicitly with --config.")
        return existing[0] if existing else standard
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    raise ValueError("Pass --config with your Claude Desktop configuration file location.")


def merge_config(settings: dict, entry: dict) -> dict:
    if not isinstance(settings, dict) or not isinstance(settings.get("mcpServers", {}), dict):
        raise ValueError("Claude config must be an object with an mcpServers object; no changes made.")
    return settings | {"mcpServers": settings.get("mcpServers", {}) | {SERVER_NAME: entry}}


def configure(path: Path, entry: dict) -> Path | None:
    original = path.read_bytes() if path.exists() else None
    settings = json.loads(original.decode("utf-8-sig")) if original is not None else {}
    updated = merge_config(settings, entry)
    if updated == settings:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if original is not None:
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        backup = path.with_name(path.name + ".backup-" + suffix)
        backup.write_bytes(original)
    temporary = path.with_name(path.name + ".tmp-" + uuid4().hex)
    try:
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--config", type=Path, help="Override automatic Claude config detection.")
    parser.add_argument("--print-config", action="store_true", help="Print the entry only; do not change Claude.")
    args = parser.parse_args()
    entry = server_entry(args.python)
    if args.print_config:
        print(json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2))
        return
    config = args.config or find_config()
    backup = configure(config, entry)
    print(f"Configured {SERVER_NAME} in {config}")
    if backup:
        print(f"Previous settings backed up to {backup}")
    print("Fully quit and reopen Claude Desktop to load the connector.")


if __name__ == "__main__":
    main()
