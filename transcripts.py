"""Shorts reports, caption parsing, local transcription, and portable exports."""
from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import yt_dlp
from yt_dlp.networking import Request

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_SHORTS = 200


class ReportCancelled(Exception):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    video_id: str
    title: str
    url: str
    view_count: int | None = None
    date_posted: str = ""
    collected_at: str = ""
    source: str = ""
    language: str = ""
    status: str = "Unavailable"
    error: str = ""
    segments: list[Segment] = field(default_factory=list)

    @property
    def transcript(self) -> str:
        return " ".join(segment.text for segment in self.segments)


def normalize_target(value: str) -> tuple[str, str]:
    """Return (video/channel, canonical URL); never accept arbitrary hosts."""
    value = value.strip()
    if value.startswith("@"):
        value = "https://www.youtube.com/" + value
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise ValueError("Paste a YouTube Shorts link, channel link, or @handle.")
    host = (parsed.hostname or "").lower()
    if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}:
        raise ValueError("Use a youtube.com or youtu.be link, or a channel @handle.")
    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if host.endswith("youtu.be") and len(parts) == 1:
        video_id = parts[0]
    elif len(parts) == 2 and parts[0] in {"shorts", "embed"}:
        video_id = parts[1]
    elif parts == ["watch"]:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if VIDEO_ID.fullmatch(video_id):
        return "video", f"https://www.youtube.com/shorts/{video_id}"
    channel = ""
    if parts and parts[0].startswith("@") and len(parts[0]) > 1:
        channel = parts[0]
        suffix = parts[1:]
    elif len(parts) >= 2 and parts[0] in {"channel", "c", "user"}:
        channel = "/".join(parts[:2])
        suffix = parts[2:]
    else:
        suffix = []
    if channel and (not suffix or suffix in [["shorts"], ["videos"], ["featured"], ["streams"]]):
        return "channel", f"https://www.youtube.com/{channel}/shorts"
    raise ValueError("That link is not a video or channel. Paste a Shorts link or a channel @handle.")


def clean_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).split())


def compact_segments(segments: list[Segment]) -> list[Segment]:
    """Remove repeated rolling captions, preserving speech repeated later."""
    result: list[Segment] = []
    previous: list[str] = []
    previous_start = previous_end = -1.0
    for segment in segments:
        text = clean_text(segment.text)
        if not text:
            continue
        current = text.split()
        if previous and (segment.start < previous_end or segment.start == previous_start):
            for overlap in range(min(len(previous), len(current)), 0, -1):
                if previous[-overlap:] == current[:overlap]:
                    text = " ".join(current[overlap:])
                    break
        previous, previous_start, previous_end = current, segment.start, segment.end
        if text:
            result.append(Segment(max(0, segment.start), max(segment.start, segment.end), text))
    return result


def parse_json3(payload: str) -> list[Segment]:
    segments = []
    for event in json.loads(payload).get("events", []):
        text = "".join(part.get("utf8", "") for part in event.get("segs", []))
        start = float(event.get("tStartMs", 0)) / 1000
        end = start + max(0, float(event.get("dDurationMs", 0))) / 1000
        segments.append(Segment(start, end, text))
    return compact_segments(segments)


def caption_seconds(value: str) -> float:
    fields = value.replace(",", ".").split(":")
    return sum(float(part) * 60 ** power for power, part in enumerate(reversed(fields)))


def parse_vtt(payload: str) -> list[Segment]:
    segments = []
    for block in re.split(r"\n\s*\n", payload.replace("\r\n", "\n")):
        lines = block.splitlines()
        for index, line in enumerate(lines):
            match = re.match(r"([\d:.]+)\s+-->\s+([\d:.]+)", line)
            if match:
                segments.append(Segment(caption_seconds(match[1]), caption_seconds(match[2]),
                                        " ".join(lines[index + 1:])))
                break
    return compact_segments(segments)


def caption_tracks(info: dict):
    native = info.get("language") or info.get("original_language") or ""
    for key, source in (("subtitles", "YouTube captions"), ("automatic_captions", "YouTube auto-captions")):
        tracks = info.get(key) or {}
        languages = [language for language in tracks if language != "live_chat"]
        languages.sort(key=lambda language: (
            0 if native and language.split("-")[0] == native.split("-")[0] else
            1 if language.endswith("-orig") else 2 if language.startswith("en") else 3,
            language,
        ))
        for language in languages:
            formats = tracks[language]
            track = next((item for ext in ("json3", "vtt") for item in formats
                          if item.get("ext") == ext and item.get("url")), None)
            if track:
                yield source, language, track


def format_date(info: dict) -> str:
    date = info.get("upload_date") or info.get("release_date")
    if date:
        try:
            return datetime.strptime(str(date), "%Y%m%d").date().isoformat()
        except ValueError:
            pass
    timestamp = info.get("timestamp")
    if isinstance(timestamp, (int, float)):
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return ""


class QuietLogger:
    def debug(self, message):
        pass

    info = debug
    warning = debug
    error = debug


class ReportService:
    def __init__(self, *, ffmpeg: Path | None = None, deno: Path | None = None,
                 cancel: threading.Event | None = None, ydl_factory=None, transcriber=None):
        self.ffmpeg = ffmpeg
        self.deno = deno
        self.cancel = cancel or threading.Event()
        self.ydl_factory = ydl_factory or yt_dlp.YoutubeDL
        self.transcriber = transcriber
        self.model = None
        self.model_error = ""

    def check_cancel(self, *_):
        if self.cancel.is_set():
            raise ReportCancelled()

    def options(self) -> dict:
        options = {"quiet": True, "no_warnings": True, "logger": QuietLogger(),
                   "noplaylist": True, "skip_download": True, "cachedir": False,
                   "socket_timeout": 20, "retries": 1, "extractor_retries": 1,
                   "fragment_retries": 1, "progress_hooks": [self.check_cancel],
                   "ignore_no_formats_error": True}
        if self.ffmpeg:
            options["ffmpeg_location"] = str(self.ffmpeg.parent)
        if self.deno:
            options["js_runtimes"] = {"deno": {"path": str(self.deno)}}
        return options

    def collect(self, target: str, count: int, audio_fallback: bool,
                on_result: Callable[[TranscriptResult], None],
                on_status: Callable[[str], None] = lambda value: None,
                on_progress: Callable[[int, int], None] = lambda done, total: None) -> list[TranscriptResult]:
        kind, url = normalize_target(target)
        if not isinstance(count, int) or not 1 <= count <= MAX_SHORTS:
            raise ValueError(f"Choose between 1 and {MAX_SHORTS} Shorts.")
        self.check_cancel()
        if kind == "video":
            entries = [{"id": url.rsplit("/", 1)[-1]}]
        else:
            on_status("Reading the channel's latest Shorts…")
            options = self.options() | {"extract_flat": "in_playlist", "lazy_playlist": True,
                                        "playlistend": count, "noplaylist": False}
            with self.ydl_factory(options) as ydl:
                listing = ydl.extract_info(url, download=False)
                self.check_cancel()
                if not listing:
                    raise ValueError("The channel could not be read. Check the link and try again.")
                entries = list(islice(listing.get("entries") or [], count))
            if not entries:
                raise ValueError("No public Shorts were found on that channel.")
        results = []
        total = len(entries)
        on_progress(0, total)
        for index, entry in enumerate(entries, 1):
            self.check_cancel()
            entry = entry or {}
            video_id = str(entry.get("id") or "")
            if not VIDEO_ID.fullmatch(video_id):
                result = TranscriptResult(video_id="", title=entry.get("title") or "Unavailable Short",
                                          url="", error="This channel entry is unavailable.")
            else:
                on_status(f"Short {index}/{total}: fetching details and captions…")
                result = self.collect_video(video_id, entry.get("title") or video_id,
                                            audio_fallback, on_status)
            results.append(result)
            on_result(result)
            on_progress(index, total)
            if index < total and self.cancel.wait(0.75):
                raise ReportCancelled()
        return results

    def collect_video(self, video_id: str, title: str, audio_fallback: bool,
                      on_status: Callable[[str], None]) -> TranscriptResult:
        result = TranscriptResult(video_id, title, f"https://www.youtube.com/shorts/{video_id}")
        try:
            with self.ydl_factory(self.options()) as ydl:
                info = ydl.extract_info(result.url, download=False)
                self.check_cancel()
                if not info or info.get("_type") in {"playlist", "multi_video"}:
                    raise ValueError("YouTube did not return video details.")
                result.title = info.get("title") or title
                count = info.get("view_count")
                result.view_count = count if isinstance(count, int) and count >= 0 else None
                result.date_posted = format_date(info)
                result.collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                caption_error = "No usable captions were available."
                for source, language, track in islice(caption_tracks(info), 3):
                    self.check_cancel()
                    try:
                        headers = info.get("http_headers", {}) | track.get("http_headers", {})
                        with ydl.urlopen(Request(track["url"], headers=headers)) as response:
                            payload = response.read(8 * 1024 * 1024 + 1)
                        if len(payload) > 8 * 1024 * 1024:
                            raise ValueError("Caption file is unexpectedly large.")
                        self.check_cancel()
                        parser = parse_json3 if track["ext"] == "json3" else parse_vtt
                        segments = parser(payload.decode("utf-8-sig"))
                        if segments:
                            result.segments, result.source, result.language = segments, source, language
                            result.status = "Ready"
                            return result
                    except ReportCancelled:
                        raise
                    except Exception as exc:
                        caption_error = "Captions could not be read: " + str(exc)
            if audio_fallback:
                on_status(f"{result.title}: transcribing audio locally…")
                if self.transcriber:
                    segments, language = self.transcriber(result.url, on_status)
                else:
                    segments, language = self.transcribe_audio(result.url, on_status)
                self.check_cancel()
                result.segments = segments
                result.language = language
                result.source = "Local speech-to-text (Whisper base)"
                result.status = "Ready" if segments else "No speech detected"
            else:
                result.error = caption_error + " Enable audio transcription to try speech-to-text."
        except ReportCancelled:
            raise
        except Exception as exc:
            self.check_cancel()
            result.error = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
        return result

    def load_model(self, on_status: Callable[[str], None]):
        if self.model_error:
            raise RuntimeError(self.model_error)
        if self.model is None:
            on_status("Loading the speech model (first use downloads about 150 MB)…")
            try:
                from faster_whisper import WhisperModel

                cache = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "YT-downloader/models"
                self.model = WhisperModel("base", device="cpu", compute_type="int8", download_root=str(cache))
            except Exception as exc:
                self.model_error = f"Speech model could not load: {exc}"
                raise RuntimeError(self.model_error) from exc
        return self.model

    def transcribe_audio(self, url: str, on_status: Callable[[str], None]) -> tuple[list[Segment], str]:
        if self.model_error:
            raise RuntimeError(self.model_error)
        self.check_cancel()
        # Audio is temporary and is removed after transcription, including on failure.
        with tempfile.TemporaryDirectory(prefix="yt-shorts-") as temporary:
            folder = Path(temporary)
            options = self.options() | {"skip_download": False, "format": "bestaudio/best",
                                        "outtmpl": str(folder / "audio.%(ext)s"),
                                        "ignore_no_formats_error": False}
            on_status("Downloading temporary audio…")
            with self.ydl_factory(options) as ydl:
                ydl.extract_info(url, download=True)
            self.check_cancel()
            audio = next((path for path in folder.glob("audio.*")
                          if path.suffix not in {".part", ".ytdl", ".json"}), None)
            if audio is None:
                raise RuntimeError("No accessible audio was downloaded.")
            model = self.load_model(on_status)
            self.check_cancel()
            on_status("Transcribing speech on this computer…")
            generated, info = model.transcribe(str(audio), beam_size=5, vad_filter=True,
                                                     condition_on_previous_text=False)
            segments = []
            for segment in generated:
                self.check_cancel()
                if segment.text.strip():
                    segments.append(Segment(segment.start, segment.end, segment.text.strip()))
            return segments, info.language


def timestamp(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def result_text(result: TranscriptResult, timestamps: bool = False) -> str:
    views = f"{result.view_count:,}" if result.view_count is not None else "Unavailable"
    header = (f"{result.title}\n{result.url}\nPosted: {result.date_posted or 'Unavailable'} | Views: {views}\n"
              f"Collected (UTC): {result.collected_at or 'Unavailable'}\n"
              f"Transcript: {result.source or result.status} | Language: {result.language or 'Unknown'}")
    if timestamps:
        text = "\n".join(f"[{timestamp(segment.start)}] {segment.text}" for segment in result.segments)
    else:
        text = result.transcript
    return header + "\n\n" + (text or result.error or result.status)


def csv_safe(value):
    # Titles/transcripts are untrusted text, never spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_export(results: list[TranscriptResult], kind: str, timestamps: bool = False) -> str:
    if kind == "json":
        return json.dumps([asdict(result) | {"transcript": result.transcript} for result in results],
                          ensure_ascii=False, indent=2)
    if kind == "txt":
        return ("\n\n" + "─" * 72 + "\n\n").join(result_text(result, timestamps) for result in results)
    if kind != "csv":
        raise ValueError("Choose CSV, TXT, or JSON.")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["title", "url", "date_posted", "view_count", "collected_at_utc", "status",
                     "transcript_source", "language", "transcript", "error"])
    for result in results:
        text = ("\n".join(f"[{timestamp(s.start)}] {s.text}" for s in result.segments)
                if timestamps else result.transcript)
        writer.writerow([csv_safe(value) for value in [result.title, result.url, result.date_posted,
                         result.view_count, result.collected_at, result.status, result.source,
                         result.language, text, result.error]])
    return output.getvalue()
