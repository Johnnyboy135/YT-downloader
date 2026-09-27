"""Offline smoke check for the packaged application (used by Windows CI)."""
import json
import traceback
from pathlib import Path


def run(app_class, destination: str) -> int:
    app = None
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from faster_whisper.vad import get_speech_timestamps
        from transcripts import Segment, TranscriptResult, render_export

        assert WhisperModel is not None
        assert get_speech_timestamps(np.zeros(16000, dtype=np.float32)) == []
        app = app_class()
        app.withdraw()
        panel = app.transcript_panel
        row = TranscriptResult("abcdefghijk", "Smoke test", "https://youtube.com/shorts/abcdefghijk",
                               view_count=1234, date_posted="2026-09-26", source="Test captions", status="Ready",
                               segments=[Segment(0, 2, "A transcript preview.")])
        panel.events.put(("result", row))
        panel.events.put(("finished", None))
        app.update()
        panel.after_cancel(panel.after_id)
        panel.drain_events()
        app.update()
        assert len(panel.table.get_children()) == 1
        assert "A transcript preview." in panel.preview.get("1.0", "end")
        assert "1234" in render_export(panel.results, "csv")
        report = {"ok": True, "checks": ["speech libraries", "bundled VAD model", "transcripts tab", "CSV export"]}
        exit_code = 0
    except Exception:
        report = {"ok": False, "error": traceback.format_exc()}
        exit_code = 1
    finally:
        if app is not None:
            app.destroy()
    Path(destination).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return exit_code
