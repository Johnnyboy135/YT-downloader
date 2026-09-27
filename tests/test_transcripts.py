import csv
import io
import json
import threading
import unittest
from unittest.mock import patch

from transcripts import (ReportCancelled, ReportService, Segment, TranscriptResult,
                         compact_segments, format_date, normalize_target, parse_json3,
                         parse_vtt, render_export, result_text)

ID1, ID2, ID3 = "abcdefghijk", "lmnopqrstuv", "0123456789_"
CAPTIONS = json.dumps({"events": [{"tStartMs": 1250, "dDurationMs": 1500,
                                  "segs": [{"utf8": "Hello "}, {"utf8": "world"}]}]})


class FakeYDL:
    def __init__(self, backend, options):
        self.backend, self.options = backend, options
        backend.options.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def extract_info(self, url, download=False):
        self.backend.urls.append(url)
        result = self.backend.responses[url]
        if isinstance(result, Exception):
            raise result
        return result

    def urlopen(self, request):
        if self.backend.caption_error:
            raise self.backend.caption_error
        return io.BytesIO(CAPTIONS.encode())


class Backend:
    def __init__(self, responses, caption_error=None):
        self.responses, self.caption_error = responses, caption_error
        self.options, self.urls = [], []

    def __call__(self, options):
        return FakeYDL(self, options)


def video_info(video_id=ID1):
    return {"id": video_id, "title": "Example Short", "upload_date": "20260924", "view_count": 12034,
            "subtitles": {"en": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext"}]}}


class InputTests(unittest.TestCase):
    def test_video_links_and_share_queries(self):
        for url in (f"https://youtube.com/shorts/{ID1}?si=tracking", f"https://youtu.be/{ID1}",
                    f"https://www.youtube.com/watch?v={ID1}&list=some-playlist"):
            self.assertEqual(normalize_target(url), ("video", f"https://www.youtube.com/shorts/{ID1}"))

    def test_channel_tabs_normalize_to_shorts(self):
        for value in ("@creator", "youtube.com/@creator/videos", "https://www.youtube.com/@creator/shorts"):
            self.assertEqual(normalize_target(value), ("channel", "https://www.youtube.com/@creator/shorts"))
        self.assertEqual(normalize_target("https://youtube.com/channel/UC123"),
                         ("channel", "https://www.youtube.com/channel/UC123/shorts"))

    def test_invalid_inputs_do_not_reach_network(self):
        for value in ("https://youtube.com.evil.test/shorts/abcdefghijk", "file:///etc/passwd",
                      "https://evil.test/@creator", "https://youtube.com/playlist?list=123",
                      "https://youtube.com/shorts/invalid", "https://someone@youtube.com/@creator"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_target(value)


class CaptionTests(unittest.TestCase):
    def test_json3_joins_words_and_converts_milliseconds(self):
        self.assertEqual(parse_json3(CAPTIONS), [Segment(1.25, 2.75, "Hello world")])

    def test_vtt_handles_cue_ids_tags_and_settings(self):
        data = 'WEBVTT\n\ncue-1\n00:00:01.250 --> 00:00:02.750 align:start\n<c>Hello &amp; world</c>\n'
        self.assertEqual(parse_vtt(data), [Segment(1.25, 2.75, "Hello & world")])

    def test_rolling_captions_are_not_repeated(self):
        segments = [Segment(0, 3, "Hello"), Segment(1, 4, "Hello world"),
                    Segment(2, 5, "Hello world again"), Segment(10, 11, "Hello world again")]
        self.assertEqual([s.text for s in compact_segments(segments)],
                         ["Hello", "world", "again", "Hello world again"])

    def test_dates_do_not_invent_missing_metadata(self):
        self.assertEqual(format_date({"upload_date": "20260924"}), "2026-09-24")
        self.assertEqual(format_date({"upload_date": "not-a-date"}), "")
        self.assertEqual(format_date({}), "")


class CollectionTests(unittest.TestCase):
    def test_single_video_returns_caption_and_metadata_without_audio(self):
        backend = Backend({f"https://www.youtube.com/shorts/{ID1}": video_info()})
        fallback = unittest.mock.Mock()
        collected = []
        results = ReportService(ydl_factory=backend, transcriber=fallback).collect(
            f"https://youtu.be/{ID1}", 10, True, collected.append)
        self.assertEqual(len(results), 1)
        self.assertEqual(collected, results)
        self.assertEqual(results[0].transcript, "Hello world")
        self.assertEqual(results[0].view_count, 12034)
        self.assertEqual(results[0].date_posted, "2026-09-24")
        self.assertTrue(results[0].collected_at.endswith("+00:00"))
        fallback.assert_not_called()

    def test_channel_limits_entries_and_preserves_latest_order(self):
        url = "https://www.youtube.com/@creator/shorts"
        backend = Backend({url: {"entries": iter([{"id": ID2}, {"id": ID1}, {"id": ID3}])},
                           f"https://www.youtube.com/shorts/{ID1}": video_info(ID1),
                           f"https://www.youtube.com/shorts/{ID2}": video_info(ID2)})
        service = ReportService(ydl_factory=backend)
        with patch.object(service.cancel, "wait", return_value=False):
            results = service.collect("@creator", 2, False, lambda result: None)
        self.assertEqual([r.video_id for r in results], [ID2, ID1])
        self.assertEqual(backend.options[0]["playlistend"], 2)
        self.assertEqual(backend.urls[0], url)
        self.assertEqual(len(backend.urls), 3)

    def test_failure_is_a_row_and_later_videos_continue(self):
        backend = Backend({"https://www.youtube.com/@creator/shorts": {"entries": [{"id": ID1}, {"id": ID2}]},
                           f"https://www.youtube.com/shorts/{ID1}": RuntimeError("Video unavailable"),
                           f"https://www.youtube.com/shorts/{ID2}": video_info(ID2)})
        service = ReportService(ydl_factory=backend)
        with patch.object(service.cancel, "wait", return_value=False):
            results = service.collect("@creator", 2, False, lambda result: None)
        self.assertEqual(results[0].status, "Unavailable")
        self.assertIsNone(results[0].view_count)
        self.assertEqual(results[1].status, "Ready")

    def test_missing_captions_can_use_audio(self):
        info = video_info()
        info["subtitles"] = {}
        backend = Backend({f"https://www.youtube.com/shorts/{ID1}": info})
        fallback = unittest.mock.Mock(return_value=([Segment(0, 1, "Spoken text")], "en"))
        results = ReportService(ydl_factory=backend, transcriber=fallback).collect(
            f"https://youtu.be/{ID1}", 1, True, lambda result: None)
        self.assertEqual(results[0].transcript, "Spoken text")
        self.assertIn("speech-to-text", results[0].source)
        self.assertEqual(results[0].view_count, 12034)
        fallback.assert_called_once()

    def test_caption_failure_without_fallback_keeps_metadata_and_reason(self):
        backend = Backend({f"https://www.youtube.com/shorts/{ID1}": video_info()}, RuntimeError("HTTP 429"))
        result = ReportService(ydl_factory=backend).collect(f"https://youtu.be/{ID1}", 1, False, lambda row: None)[0]
        self.assertEqual(result.date_posted, "2026-09-24")
        self.assertIn("HTTP 429", result.error)
        self.assertEqual(result.transcript, "")

    def test_cancel_keeps_already_emitted_results(self):
        backend = Backend({"https://www.youtube.com/@creator/shorts": {"entries": [{"id": ID1}, {"id": ID2}]},
                           f"https://www.youtube.com/shorts/{ID1}": video_info()})
        cancel, received = threading.Event(), []
        def receive(row):
            received.append(row)
            cancel.set()
        with self.assertRaises(ReportCancelled):
            ReportService(ydl_factory=backend, cancel=cancel).collect("@creator", 2, False, receive)
        self.assertEqual(len(received), 1)
        self.assertEqual(len(backend.urls), 2)

    def test_count_validation_before_network(self):
        backend = Backend({})
        for count in (0, -1, 201, "10"):
            with self.assertRaises(ValueError):
                ReportService(ydl_factory=backend).collect("@creator", count, False, lambda row: None)
        self.assertEqual(backend.urls, [])


class ExportTests(unittest.TestCase):
    def test_csv_quotes_text_and_neutralizes_formulas(self):
        row = TranscriptResult(ID1, '=HYPERLINK("evil")', "https://youtube.com/shorts/" + ID1,
                               view_count=0, segments=[Segment(65, 67, "A comma, and \"quotes\"")])
        data = list(csv.DictReader(io.StringIO(render_export([row], "csv", True))))
        self.assertTrue(data[0]["title"].startswith("'="))
        self.assertEqual(data[0]["view_count"], "0")
        self.assertEqual(data[0]["transcript"], '[01:05] A comma, and "quotes"')

    def test_json_retains_segments_nulls_and_unicode(self):
        row = TranscriptResult(ID1, "日本語", "", segments=[Segment(1.25, 2.5, "こんにちは")])
        decoded = json.loads(render_export([row], "json"))[0]
        self.assertIsNone(decoded["view_count"])
        self.assertEqual(decoded["segments"][0]["start"], 1.25)
        self.assertEqual(decoded["transcript"], "こんにちは")

    def test_text_export_shows_missing_fields_and_error(self):
        row = TranscriptResult(ID1, "Example", "", error="Captions unavailable")
        self.assertIn("Views: Unavailable", result_text(row))
        self.assertIn("Captions unavailable", render_export([row], "txt"))


if __name__ == "__main__":
    unittest.main()
