import json
import subprocess
import sys
import threading
import time
import unittest
from dataclasses import asdict
from unittest.mock import patch

from mcp_extraction import BoundedReportService, run_worker
from transcripts import ReportCancelled, Segment, TranscriptResult


class WorkerTests(unittest.TestCase):
    def test_timeout_terminates_a_stuck_process(self):
        processes = []
        real_popen = subprocess.Popen

        def spawn(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with patch("mcp_extraction.subprocess.Popen", side_effect=spawn):
            with self.assertRaisesRegex(TimeoutError, "time limit"):
                run_worker({"action": "captions"}, cancel=threading.Event(), timeout=1,
                           command=[sys.executable, "-c", "import time; time.sleep(60)"])
        self.assertIsNotNone(processes[0].poll())
        self.assertLess(time.monotonic() - started, 8)

    def test_cancel_interrupts_worker_and_retains_received_metadata(self):
        cancel = threading.Event()
        received = []
        def metadata(row):
            received.append(row)
            cancel.set()
        message = json.dumps({"kind": "metadata", "data": {"title": "Known title", "view_count": 123}})
        code = f"import time; print({message!r}, flush=True); time.sleep(60)"
        started = time.monotonic()
        with self.assertRaises(ReportCancelled):
            run_worker({"action": "captions"}, cancel=cancel, timeout=30, on_metadata=metadata,
                       command=[sys.executable, "-c", code])
        self.assertEqual(received[0]["view_count"], 123)
        self.assertLess(time.monotonic() - started, 8)

    def test_worker_exit_does_not_wait_until_deadline(self):
        with self.assertRaisesRegex(RuntimeError, "exited without a result"):
            run_worker({"action": "captions"}, cancel=threading.Event(), timeout=30,
                       command=[sys.executable, "-c", "pass"])


class FallbackTests(unittest.TestCase):
    def test_caption_timeout_enters_audio_fallback_and_keeps_metadata(self):
        service = BoundedReportService()
        actions = []
        def operation(action, timeout, on_status=lambda _: None, on_metadata=lambda _: None, **values):
            actions.append(action)
            if action == "captions":
                on_metadata(asdict(TranscriptResult("abcdefghijk", "Known", "https://youtube.com/shorts/abcdefghijk",
                                                   view_count=456, date_posted="2026-09-26")))
                raise TimeoutError("captions step exceeded its 45-second time limit")
            return {"segments": [asdict(Segment(0, 1, "Recovered speech"))], "language": "en"}
        with patch.object(service, "operation", side_effect=operation):
            result = service.collect_video("abcdefghijk", "Initial", True, lambda _: None)
        self.assertEqual(actions, ["captions", "audio"])
        self.assertEqual((result.transcript, result.view_count, result.date_posted),
                         ("Recovered speech", 456, "2026-09-26"))
        self.assertEqual(result.error, "")

    def test_caption_timeout_without_fallback_reports_error(self):
        service = BoundedReportService()
        with patch.object(service, "operation", side_effect=TimeoutError("captions timed out")) as operation:
            result = service.collect_video("abcdefghijk", "Known", False, lambda _: None)
        self.assertEqual(result.error, "captions timed out")
        self.assertEqual(operation.call_count, 1)

    def test_failed_video_does_not_block_the_next_video(self):
        service = BoundedReportService()
        rows = []
        def operation(action, timeout, *args, **values):
            if values["video_id"] == "abcdefghijk":
                raise TimeoutError("captions timed out")
            return asdict(TranscriptResult("lmnopqrstuv", "Next", "", status="Ready",
                                            segments=[Segment(0, 1, "Next transcript")]))
        with patch.object(service, "list_entries", return_value=[{"id": "abcdefghijk"}, {"id": "lmnopqrstuv"}]), \
             patch.object(service, "operation", side_effect=operation), \
             patch.object(service.cancel, "wait", return_value=False):
            service.collect("@creator", 2, False, rows.append)
        self.assertIn("timed out", rows[0].error)
        self.assertEqual(rows[1].transcript, "Next transcript")
