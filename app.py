from __future__ import annotations

import queue
import re
import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import yt_dlp
except ImportError:  # pragma: no cover - handled in GUI startup
    yt_dlp = None


YOUTUBE_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/.+",
    re.IGNORECASE,
)


def find_ffmpeg() -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return Path(ffmpeg)

    candidates = [
        Path.home() / ".stacher" / "ffmpeg.exe",
        Path.home() / "scoop" / "shims" / "ffmpeg.exe",
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
    ]

    for candidate in candidates:
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            matches = list(candidate.glob("**/ffmpeg.exe"))
            if matches:
                return matches[0]

    return None


class DownloadApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("YouTube Downloader")
        self.geometry("820x620")
        self.minsize(720, 540)

        self.messages: queue.Queue[tuple[str, str | float]] = queue.Queue()
        self.worker: threading.Thread | None = None

        default_downloads = Path.home() / "Downloads"
        self.url_var = tk.StringVar()
        self.folder_var = tk.StringVar(value=str(default_downloads if default_downloads.exists() else Path.cwd()))
        self.mode_var = tk.StringVar(value="video")
        self.video_quality_var = tk.StringVar(value="Best available")
        self.audio_format_var = tk.StringVar(value="m4a")
        self.status_var = tk.StringVar(value="Ready")
        self.progress_var = tk.DoubleVar(value=0)
        self.ffmpeg_path = find_ffmpeg()

        self._build_ui()
        self._set_mode_state()
        self.after(100, self._drain_messages)

        if yt_dlp is None:
            self._log("Install dependencies with: pip install -r requirements.txt")
            messagebox.showerror(
                "Missing dependency",
                "yt-dlp is not installed.\n\nRun:\npip install -r requirements.txt",
            )
        elif self.ffmpeg_path:
            self._log(f"ffmpeg found: {self.ffmpeg_path}")
        else:
            self._log("ffmpeg not found. Best-available video can still work, but audio conversion needs ffmpeg.")

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(9, weight=1)

        title = ttk.Label(root, text="YouTube Audio/Video Downloader", font=("Segoe UI", 18, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        note = ttk.Label(
            root,
            text="Use this only for videos you own, Creative Commons/public-domain media, or content you have permission to download.",
            wraplength=760,
            foreground="#555555",
        )
        note.grid(row=1, column=0, columnspan=3, sticky="we", pady=(0, 18))

        ttk.Label(root, text="YouTube URL").grid(row=2, column=0, sticky="w", pady=6)
        url_entry = ttk.Entry(root, textvariable=self.url_var)
        url_entry.grid(row=2, column=1, columnspan=2, sticky="we", pady=6)
        url_entry.focus()

        ttk.Label(root, text="Save to").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(root, textvariable=self.folder_var).grid(row=3, column=1, sticky="we", pady=6)
        ttk.Button(root, text="Browse", command=self._choose_folder).grid(row=3, column=2, sticky="e", padx=(8, 0), pady=6)

        ttk.Label(root, text="Mode").grid(row=4, column=0, sticky="w", pady=6)
        mode_frame = ttk.Frame(root)
        mode_frame.grid(row=4, column=1, columnspan=2, sticky="w", pady=6)
        ttk.Radiobutton(mode_frame, text="Video", variable=self.mode_var, value="video", command=self._set_mode_state).pack(
            side="left", padx=(0, 14)
        )
        ttk.Radiobutton(mode_frame, text="Audio only", variable=self.mode_var, value="audio", command=self._set_mode_state).pack(
            side="left"
        )

        ttk.Label(root, text="Video quality").grid(row=5, column=0, sticky="w", pady=6)
        self.video_quality = ttk.Combobox(
            root,
            textvariable=self.video_quality_var,
            values=("Best available", "1080p", "720p", "480p", "360p"),
            state="readonly",
        )
        self.video_quality.grid(row=5, column=1, sticky="w", pady=6)

        ttk.Label(root, text="Audio format").grid(row=6, column=0, sticky="w", pady=6)
        self.audio_format = ttk.Combobox(
            root,
            textvariable=self.audio_format_var,
            values=("m4a", "mp3", "opus", "wav"),
            state="readonly",
        )
        self.audio_format.grid(row=6, column=1, sticky="w", pady=6)

        controls = ttk.Frame(root)
        controls.grid(row=7, column=0, columnspan=3, sticky="we", pady=(14, 10))
        self.download_button = ttk.Button(controls, text="Download", command=self._start_download)
        self.download_button.pack(side="left")
        self.open_folder_button = ttk.Button(controls, text="Open Folder", command=self._open_folder)
        self.open_folder_button.pack(side="left", padx=(10, 0))
        ttk.Label(controls, textvariable=self.status_var).pack(side="left", padx=(16, 0))

        self.progress = ttk.Progressbar(root, variable=self.progress_var, maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, sticky="we", pady=(0, 8))

        log_frame = ttk.LabelFrame(root, text="Log", padding=8)
        log_frame.grid(row=9, column=0, columnspan=3, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if selected:
            self.folder_var.set(selected)

    def _open_folder(self) -> None:
        folder = Path(self.folder_var.get()).expanduser()
        if not folder.exists():
            messagebox.showwarning("Folder not found", "Choose an existing output folder first.")
            return
        try:
            import os

            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - platform-specific
            messagebox.showerror("Could not open folder", str(exc))

    def _set_mode_state(self) -> None:
        if self.mode_var.get() == "video":
            self.video_quality.configure(state="readonly")
            self.audio_format.configure(state="disabled")
        else:
            self.video_quality.configure(state="disabled")
            self.audio_format.configure(state="readonly")

    def _start_download(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        if yt_dlp is None:
            messagebox.showerror("Missing dependency", "Run: pip install -r requirements.txt")
            return

        url = self.url_var.get().strip()
        folder = Path(self.folder_var.get()).expanduser()

        if not YOUTUBE_URL_RE.match(url):
            messagebox.showwarning("Invalid URL", "Enter a valid youtube.com or youtu.be URL.")
            return
        if not folder.exists() or not folder.is_dir():
            messagebox.showwarning("Invalid folder", "Choose an existing output folder.")
            return

        needs_ffmpeg = self.mode_var.get() == "audio" or self.video_quality_var.get() != "Best available"
        self.ffmpeg_path = find_ffmpeg()
        if needs_ffmpeg and self.ffmpeg_path is None:
            messagebox.showwarning(
                "ffmpeg not found",
                "This option needs ffmpeg for merging or conversion. Install ffmpeg, or use video mode with Best available.",
            )
            return

        self.progress_var.set(0)
        self.download_button.configure(state="disabled")
        self.status_var.set("Downloading...")
        self._log(f"Starting: {url}")

        self.worker = threading.Thread(target=self._download, args=(url, folder), daemon=True)
        self.worker.start()

    def _download(self, url: str, folder: Path) -> None:
        try:
            options = self._yt_dlp_options(folder)
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader.download([url])
            self.messages.put(("status", "Done"))
            self.messages.put(("progress", 100.0))
            self.messages.put(("log", "Download complete."))
        except Exception as exc:
            self.messages.put(("status", "Failed"))
            self.messages.put(("log", f"Error: {exc}"))
        finally:
            self.messages.put(("enable", ""))

    def _yt_dlp_options(self, folder: Path) -> dict:
        output_template = str(folder / "%(title).180s [%(id)s].%(ext)s")
        options: dict = {
            "outtmpl": output_template,
            "noplaylist": True,
            "progress_hooks": [self._progress_hook],
            "windowsfilenames": True,
            "quiet": True,
            "no_warnings": False,
        }
        if self.ffmpeg_path:
            options["ffmpeg_location"] = str(self.ffmpeg_path.parent)

        if self.mode_var.get() == "audio":
            audio_format = self.audio_format_var.get()
            options["format"] = "bestaudio/best"
            options["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": audio_format,
                    "preferredquality": "192",
                }
            ]
        else:
            quality = self.video_quality_var.get()
            if quality == "Best available":
                if self.ffmpeg_path is None:
                    options["format"] = "best[ext=mp4]/best"
                else:
                    options["format"] = "bestvideo*+bestaudio/best"
            else:
                height = quality.removesuffix("p")
                options["format"] = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
            options["merge_output_format"] = "mp4"

        return options

    def _progress_hook(self, data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            percent = self._percent_from_progress(data)
            if percent is not None:
                self.messages.put(("progress", percent))
                self.messages.put(("status", f"Downloading... {percent:.1f}%"))
        elif status == "finished":
            filename = data.get("filename", "file")
            self.messages.put(("status", "Processing..."))
            self.messages.put(("log", f"Downloaded: {filename}"))

    @staticmethod
    def _percent_from_progress(data: dict) -> float | None:
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        downloaded = data.get("downloaded_bytes")
        if not total or downloaded is None:
            return None
        return max(0.0, min(100.0, downloaded * 100 / total))

    def _drain_messages(self) -> None:
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self._log(str(value))
            elif kind == "status":
                self.status_var.set(str(value))
            elif kind == "progress":
                self.progress_var.set(float(value))
            elif kind == "enable":
                self.download_button.configure(state="normal")

        self.after(100, self._drain_messages)

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


if __name__ == "__main__":
    app = DownloadApp()
    app.mainloop()
