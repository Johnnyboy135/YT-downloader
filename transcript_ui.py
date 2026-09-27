"""Tk interface for individual Shorts and channel transcript reports."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from transcripts import MAX_SHORTS, ReportCancelled, ReportService, normalize_target, render_export, result_text


class TranscriptPanel(ttk.Frame):
    def __init__(self, parent, *, ffmpeg=None, deno=None):
        super().__init__(parent, padding=18)
        self.ffmpeg, self.deno = ffmpeg, deno
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.results = []
        self.target = tk.StringVar()
        self.count = tk.StringVar(value="10")
        self.audio_fallback = tk.BooleanVar(value=True)
        self.timestamps = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self.progress = tk.DoubleVar(value=0)
        self.build_ui()
        self.after_id = self.after(100, self.drain_events)

    def build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        ttk.Label(self, text="Shorts transcripts", font=("Segoe UI", 18, "bold")).grid(row=0, sticky="w")
        ttk.Label(self, text="Paste one Short, or a channel link / @handle to collect its latest Shorts.",
                  wraplength=900).grid(row=1, sticky="w", pady=(6, 14))
        inputs = ttk.Frame(self)
        inputs.grid(row=2, sticky="ew")
        inputs.columnconfigure(0, weight=1)
        ttk.Label(inputs, text="Shorts link or channel").grid(row=0, column=0, sticky="w")
        ttk.Label(inputs, text="Number of Shorts").grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.url_entry = ttk.Entry(inputs, textvariable=self.target)
        self.url_entry.grid(row=1, column=0, sticky="ew", pady=4)
        self.count_entry = ttk.Spinbox(inputs, from_=1, to=MAX_SHORTS, width=8, textvariable=self.count)
        self.count_entry.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=4)
        ttk.Label(inputs, text=f"One video link = one transcript. Channel reports support 1–{MAX_SHORTS} Shorts.").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 8))

        controls = ttk.Frame(self)
        controls.grid(row=3, sticky="ew")
        self.audio_check = ttk.Checkbutton(controls, text="Transcribe audio locally if captions are unavailable",
                                          variable=self.audio_fallback)
        self.audio_check.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(controls, text="First audio transcription downloads a speech model (~150 MB). No API key needed.",
                  foreground="#555555").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))
        self.start_button = ttk.Button(controls, text="Get transcripts", command=self.start)
        self.start_button.grid(row=2, column=0, sticky="w")
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self.cancel_report, state="disabled")
        self.cancel_button.grid(row=2, column=1, padx=(8, 16))
        ttk.Checkbutton(controls, text="Include timestamps", variable=self.timestamps,
                        command=self.show_selected).grid(row=2, column=2, sticky="w")

        progress = ttk.Frame(self)
        progress.grid(row=4, sticky="ew", pady=(10, 8))
        progress.columnconfigure(0, weight=1)
        ttk.Progressbar(progress, variable=self.progress, maximum=100).grid(row=0, sticky="ew")
        ttk.Label(progress, textvariable=self.status, wraplength=900).grid(row=1, sticky="w", pady=(4, 0))

        panes = ttk.Panedwindow(self, orient="vertical")
        panes.grid(row=5, sticky="nsew")
        table_frame = ttk.Frame(panes)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("title", "posted", "views", "status")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6, selectmode="browse")
        for name, label, width in [("title", "Short", 420), ("posted", "Date posted", 110),
                                    ("views", "Views", 100), ("status", "Transcript", 170)]:
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=80, stretch=name == "title")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", self.show_selected)
        panes.add(table_frame, weight=1)
        preview_frame = ttk.LabelFrame(panes, text="Transcript and details", padding=8)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview = tk.Text(preview_frame, wrap="word", height=8, state="disabled")
        self.preview.grid(row=0, column=0, sticky="nsew")
        preview_scroll = ttk.Scrollbar(preview_frame, command=self.preview.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=preview_scroll.set)
        panes.add(preview_frame, weight=2)

        footer = ttk.Frame(self)
        footer.grid(row=6, sticky="ew", pady=(10, 0))
        self.copy_button = ttk.Button(footer, text="Copy selected", command=self.copy_selected, state="disabled")
        self.copy_button.pack(side="left")
        self.export_buttons = []
        for label, kind in (("Export CSV (Excel)", "csv"), ("Export text", "txt"), ("Export JSON", "json")):
            button = ttk.Button(footer, text=label, command=lambda kind=kind: self.export(kind), state="disabled")
            button.pack(side="left", padx=(8, 0))
            self.export_buttons.append(button)
        ttk.Label(self, text="Views are a snapshot when collected. Missing captions, dates, or counts are marked unavailable.",
                  foreground="#555555", wraplength=900).grid(row=7, sticky="w", pady=(8, 0))

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        try:
            kind, target = normalize_target(self.target.get())
            count = int(self.count.get()) if kind == "channel" else 1
            if not 1 <= count <= MAX_SHORTS:
                raise ValueError(f"Choose between 1 and {MAX_SHORTS} Shorts.")
        except ValueError as exc:
            messagebox.showwarning("Check your input", str(exc), parent=self)
            return
        self.results.clear()
        self.table.delete(*self.table.get_children())
        self.set_preview("")
        self.progress.set(0)
        self.status.set("Starting…")
        self.cancel.clear()
        self.set_running(True)
        self.copy_button.configure(state="disabled")
        for button in self.export_buttons:
            button.configure(state="disabled")
        fallback = self.audio_fallback.get()
        self.worker = threading.Thread(target=self.run_report, args=(target, count, fallback), daemon=True)
        self.worker.start()

    def run_report(self, target, count, fallback):
        service = ReportService(ffmpeg=self.ffmpeg, deno=self.deno, cancel=self.cancel)
        try:
            results = service.collect(target, count, fallback,
                                      lambda row: self.events.put(("result", row)),
                                      lambda text: self.events.put(("status", text)),
                                      lambda done, total: self.events.put(("progress", (done, total))))
            ready = sum(bool(result.segments) for result in results)
            self.events.put(("status", f"Finished: {ready}/{len(results)} transcripts ready. Select a row for details."))
        except ReportCancelled:
            self.events.put(("status", "Cancelled. Completed results are available to export."))
        except Exception as exc:
            self.events.put(("status", f"Could not collect Shorts: {exc}"))
        finally:
            self.events.put(("finished", None))

    def cancel_report(self):
        self.cancel.set()
        self.status.set("Cancelling after the current network or speech-model operation…")
        self.cancel_button.configure(state="disabled")

    def set_running(self, running):
        for widget in (self.start_button, self.url_entry, self.count_entry, self.audio_check):
            widget.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")

    def drain_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "result":
                item = str(len(self.results))
                self.results.append(value)
                views = f"{value.view_count:,}" if value.view_count is not None else "Unavailable"
                self.table.insert("", "end", iid=item,
                                  values=(value.title, value.date_posted or "Unavailable", views,
                                          value.source if value.segments else value.status))
                if not self.table.selection():
                    self.table.selection_set(item)
                self.copy_button.configure(state="normal")
                for button in self.export_buttons:
                    button.configure(state="normal")
                self.show_selected()
            elif kind == "status":
                if not self.cancel.is_set() or str(value).startswith(("Cancelled", "Finished")):
                    self.status.set(value)
            elif kind == "progress":
                done, total = value
                self.progress.set(done * 100 / total if total else 0)
            elif kind == "finished":
                self.set_running(False)
        self.after_id = self.after(100, self.drain_events)

    def set_preview(self, text):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def show_selected(self, *_):
        selected = self.table.selection()
        if selected:
            self.set_preview(result_text(self.results[int(selected[0])], self.timestamps.get()))

    def copy_selected(self):
        selected = self.table.selection()
        if selected:
            self.clipboard_clear()
            self.clipboard_append(result_text(self.results[int(selected[0])], self.timestamps.get()))

    def export(self, kind):
        if not self.results:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Export Shorts report",
                                           initialfile=f"shorts-report.{kind}", defaultextension=f".{kind}",
                                           filetypes=[(kind.upper() + " report", f"*.{kind}")])
        if path:
            try:
                Path(path).write_text(render_export(self.results, kind, self.timestamps.get()),
                                      encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="")
            except OSError as exc:
                messagebox.showerror("Could not save report", str(exc), parent=self)

    def destroy(self):
        self.cancel.set()
        self.after_cancel(self.after_id)
        super().destroy()
