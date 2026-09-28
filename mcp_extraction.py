"""Interruptible MCP extraction with wall-clock limits, including subprocesses."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from transcripts import ReportCancelled, ReportService, Segment, TranscriptResult, temporary_audio_directory

LIST_TIMEOUT = 30
CAPTIONS_TIMEOUT = 45
AUDIO_TIMEOUT = 300


def stop_worker(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Only terminate the worker we created and its children (for example Deno).
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def run_worker(request: dict, *, cancel: threading.Event, timeout: float,
               on_status=lambda message: None, on_metadata=lambda row: None,
               command=None):
    if cancel.is_set():
        raise ReportCancelled()
    command = command or [sys.executable, "-u", str(Path(__file__).with_name("mcp_worker.py"))]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                               env=os.environ | {"PYTHONUTF8": "1"},
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                               start_new_session=os.name != "nt")
    messages = queue.Queue()

    def read():
        try:
            for line in process.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    deadline = time.monotonic() + timeout
    try:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.close()
        while True:
            if cancel.is_set():
                raise ReportCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{request['action']} step exceeded its {timeout:g}-second time limit.")
            try:
                line = messages.get(timeout=min(0.1, remaining))
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("Extraction worker exited without a result.")
            message = json.loads(line)
            if message["kind"] == "result":
                return message["data"]
            if message["kind"] == "error":
                raise RuntimeError(message["data"])
            if message["kind"] == "status":
                on_status(message["data"])
            elif message["kind"] == "metadata":
                on_metadata(message["data"])
    finally:
        stop_worker(process)
        reader.join(timeout=1)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


def decode_result(data: dict) -> TranscriptResult:
    return TranscriptResult(**(data | {"segments": [Segment(**segment) for segment in data["segments"]]}))


class BoundedReportService(ReportService):
    def operation(self, action, timeout, on_status=lambda message: None,
                  on_metadata=lambda row: None, **values):
        request = {"action": action, "ffmpeg": str(self.ffmpeg) if self.ffmpeg else None,
                   "deno": str(self.deno) if self.deno else None} | values
        return run_worker(request, cancel=self.cancel, timeout=timeout,
                          on_status=on_status, on_metadata=on_metadata)

    def list_entries(self, url, count):
        return self.operation("list", LIST_TIMEOUT, url=url, count=count)

    def collect_video(self, video_id, title, audio_fallback, on_status):
        result = TranscriptResult(video_id, title, f"https://www.youtube.com/shorts/{video_id}")

        def metadata(data):
            nonlocal result
            result = decode_result(data)

        on_status(f"{title}: fetching details and captions (up to {CAPTIONS_TIMEOUT} seconds)…")
        try:
            result = decode_result(self.operation("captions", CAPTIONS_TIMEOUT, on_status, metadata,
                                                  video_id=video_id, title=title))
        except ReportCancelled:
            raise
        except Exception as exc:
            result.error = str(exc)
        if result.segments or not audio_fallback:
            return result
        caption_error = result.error
        on_status(f"{result.title}: trying audio transcription (up to {AUDIO_TIMEOUT // 60} minutes; "
                  "first use downloads the speech model)…")
        try:
            with temporary_audio_directory() as temporary:
                data = self.operation("audio", AUDIO_TIMEOUT, on_status, url=result.url, temporary=str(temporary))
            result.segments = [Segment(**segment) for segment in data["segments"]]
            result.language = data["language"]
            result.source = "Local speech-to-text (Whisper base)"
            result.status = "Ready" if result.segments else "No speech detected"
            result.error = ""
        except ReportCancelled:
            raise
        except Exception as exc:
            result.error = f"{caption_error} Audio fallback failed: {exc}"
        return result
