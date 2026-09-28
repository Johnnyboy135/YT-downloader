"""One isolated extraction operation; stdout carries JSON events to its parent."""
import contextlib
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from transcripts import ReportService


def main():
    wire = sys.stdout

    def emit(kind, data):
        wire.write(json.dumps({"kind": kind, "data": data}, ensure_ascii=False) + "\n")
        wire.flush()

    try:
        request = json.loads(sys.stdin.readline())
        service = ReportService(ffmpeg=Path(request["ffmpeg"]) if request.get("ffmpeg") else None,
                                deno=Path(request["deno"]) if request.get("deno") else None)
        # Dependency diagnostics must never be confused with worker events.
        with contextlib.redirect_stdout(sys.stderr):
            status = lambda message: emit("status", message)
            if request["action"] == "list":
                entries = service.list_entries(request["url"], request["count"])
                result = [{"id": (entry or {}).get("id"), "title": (entry or {}).get("title")}
                          for entry in entries]
            elif request["action"] == "captions":
                row = service.collect_video(request["video_id"], request["title"], False, status,
                                            lambda row: emit("metadata", asdict(row)))
                result = asdict(row)
            elif request["action"] == "audio":
                # The parent owns this directory and cleans it even if this worker is killed.
                tempfile.tempdir = request["temporary"]
                segments, language = service.transcribe_audio(request["url"], status)
                result = {"segments": [asdict(segment) for segment in segments], "language": language}
            else:
                raise ValueError("Unknown extraction operation")
        emit("result", result)
    except Exception as exc:
        emit("error", str(exc))


if __name__ == "__main__":
    main()
