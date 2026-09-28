import threading
import unittest

from mcp_jobs import TranscriptJobs
from setup_claude_mcp import merge_config
from transcripts import ReportCancelled, Segment, TranscriptResult


class FakeService:
    def __init__(self, **kwargs):
        self.cancel = kwargs["cancel"]

    def collect(self, target, count, audio_fallback, on_result, on_status, on_progress):
        on_status("Reading captions")
        on_progress(0, count)
        on_result(TranscriptResult("abcdefghijk", "Example", target, view_count=0,
                                   date_posted="2026-09-26", status="Ready",
                                   segments=[Segment(0, 1.5, "Hello world")]))
        on_progress(1, count)


class JobTests(unittest.TestCase):
    def test_result_has_metadata_full_text_and_segments_and_is_isolated(self):
        jobs = TranscriptJobs(FakeService)
        started = jobs.start("@creator", 3, False)
        result = jobs.get(started["job_id"], 2)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["requested_count"], 3)
        self.assertEqual(result["target"], "https://www.youtube.com/@creator/shorts")
        row = result["results"][0]
        self.assertEqual((row["transcript"], row["view_count"], row["date_posted"]),
                         ("Hello world", 0, "2026-09-26"))
        row["segments"][0]["text"] = "changed"
        self.assertEqual(jobs.get(started["job_id"], 0)["results"][0]["segments"][0]["text"], "Hello world")

    def test_single_link_forces_one_result(self):
        jobs = TranscriptJobs(FakeService)
        started = jobs.start("https://youtu.be/abcdefghijk", 10)
        self.assertEqual(jobs.get(started["job_id"], 2)["requested_count"], 1)

    def test_invalid_input_never_starts_a_job(self):
        jobs = TranscriptJobs(FakeService)
        for count in (0, 11, True, 1.5, "2"):
            with self.assertRaises(ValueError):
                jobs.start("@creator", count)
        with self.assertRaises(ValueError):
            jobs.start("https://example.com/shorts/abcdefghijk")
        self.assertEqual(jobs.jobs, {})
        for wait in (-1, 21, True):
            with self.assertRaises(ValueError):
                jobs.get("unknown", wait)

    def test_cancellation_preserves_partial_rows_and_enforces_one_active_job(self):
        ready = threading.Event()

        class BlockingService(FakeService):
            def collect(self, *args):
                super().collect(*args)
                ready.set()
                if not self.cancel.wait(5):
                    raise RuntimeError("Test cancellation timed out")
                raise ReportCancelled()

        jobs = TranscriptJobs(BlockingService)
        started = jobs.start("@creator", 2)
        try:
            self.assertTrue(ready.wait(2))
            with self.assertRaisesRegex(ValueError, started["job_id"]):
                jobs.start("@another", 1)
            self.assertEqual(len(jobs.get(started["job_id"], 0)["results"]), 1)
            jobs.cancel_job(started["job_id"])
            result = jobs.get(started["job_id"], 2)
            self.assertEqual(result["state"], "cancelled")
            self.assertEqual(len(result["results"]), 1)
        finally:
            jobs.close()

    def test_failure_retains_partial_results(self):
        class FailingService(FakeService):
            def collect(self, *args):
                super().collect(*args)
                raise RuntimeError("YouTube unavailable")
        jobs = TranscriptJobs(FailingService)
        started = jobs.start("@creator")
        result = jobs.get(started["job_id"], 2)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "YouTube unavailable")
        self.assertEqual(len(result["results"]), 1)

    def test_completed_job_is_not_cancelled_and_history_is_bounded(self):
        jobs = TranscriptJobs(FakeService, history_limit=1)
        first = jobs.start("@creator")
        jobs.get(first["job_id"], 2)
        self.assertEqual(jobs.cancel_job(first["job_id"])["state"], "completed")
        second = jobs.start("@creator")
        jobs.get(second["job_id"], 2)
        with self.assertRaisesRegex(ValueError, "Unknown or expired"):
            jobs.get(first["job_id"], 0)
        jobs.close()
        with self.assertRaisesRegex(ValueError, "shutting down"):
            jobs.start("@creator")


class ConfigTests(unittest.TestCase):
    def test_preserves_existing_servers_preferences_and_input(self):
        original = {"mcpServers": {"existing": {"command": "keep"}}, "preferences": {"enabled": True}}
        updated = merge_config(original, {"command": "python"})
        self.assertEqual(updated["mcpServers"]["existing"], {"command": "keep"})
        self.assertEqual(updated["preferences"], original["preferences"])
        self.assertNotIn("youtube-shorts", original["mcpServers"])
        self.assertEqual(merge_config(updated, {"command": "python"}), updated)

    def test_rejects_malformed_config_instead_of_overwriting(self):
        for invalid in ([], {"mcpServers": None}, {"mcpServers": []}):
            with self.assertRaises(ValueError):
                merge_config(invalid, {})
