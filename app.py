from __future__ import annotations

import ctypes
import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

try:
    from pynput import keyboard, mouse
except ImportError:  # pragma: no cover - handled in GUI startup
    keyboard = None
    mouse = None


@dataclass
class MacroEvent:
    time: float
    type: str
    data: dict


def serialize_key(key: object) -> dict:
    char = getattr(key, "char", None)
    if char is not None:
        return {"kind": "char", "value": char}

    name = getattr(key, "name", None)
    if name:
        return {"kind": "special", "value": name}

    text = str(key)
    if text.startswith("Key."):
        return {"kind": "special", "value": text.removeprefix("Key.")}
    return {"kind": "raw", "value": text}


def deserialize_key(data: dict) -> object | None:
    if keyboard is None:
        return None

    kind = data.get("kind")
    value = data.get("value")
    if kind == "char":
        return value
    if kind == "special" and isinstance(value, str):
        return getattr(keyboard.Key, value, None)
    return None


def serialize_button(button: object) -> str:
    name = getattr(button, "name", None)
    if name:
        return str(name)
    text = str(button)
    return text.removeprefix("Button.")


def deserialize_button(value: str) -> object | None:
    if mouse is None:
        return None
    return getattr(mouse.Button, value, None)


SPECIAL_HOTKEY_NAMES = {
    "alt",
    "alt_l",
    "alt_r",
    "backspace",
    "caps_lock",
    "cmd",
    "cmd_l",
    "cmd_r",
    "ctrl",
    "ctrl_l",
    "ctrl_r",
    "delete",
    "down",
    "end",
    "enter",
    "esc",
    "home",
    "insert",
    "left",
    "menu",
    "page_down",
    "page_up",
    "pause",
    "right",
    "shift",
    "shift_l",
    "shift_r",
    "space",
    "tab",
    "up",
}
SPECIAL_HOTKEY_NAMES.update(f"f{number}" for number in range(1, 25))
MAX_RECORDED_EVENTS = 50000
MAX_LOOP_DELAY_SECONDS = 24 * 60 * 60
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
WIN32_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}
PROJECT_DIR = Path(__file__).resolve().parent
MACROS_DIR = PROJECT_DIR / "macros"
AUTOSAVE_MACRO_PATH = MACROS_DIR / "last_macro.json"


def configure_windows_input_process() -> None:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def win32_move_mouse(x: int, y: int, pulse: bool = False) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos(int(x), int(y))
    if pulse:
        user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
        user32.SetCursorPos(int(x), int(y))


def win32_mouse_button(button_name: str, pressed: bool) -> None:
    flags = WIN32_BUTTON_FLAGS.get(button_name)
    if flags is None:
        return
    ctypes.windll.user32.mouse_event(flags[0] if pressed else flags[1], 0, 0, 0, 0)


def win32_mouse_scroll(dy: int) -> None:
    ctypes.windll.user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, int(dy) * WHEEL_DELTA, 0)


def macro_payload(events: list[MacroEvent]) -> dict:
    return {
        "version": 1,
        "events": [asdict(event) for event in events],
    }


def events_from_payload(payload: dict) -> list[MacroEvent]:
    events = payload.get("events", [])
    return [MacroEvent(float(item["time"]), str(item["type"]), dict(item["data"])) for item in events]


def normalize_hotkey(value: str) -> str:
    parts = [part.strip().lower() for part in value.replace("-", "+").split("+") if part.strip()]
    if not parts:
        raise ValueError("Hotkeys cannot be blank.")

    normalized: list[str] = []
    for part in parts:
        aliases = {
            "control": "ctrl",
            "escape": "esc",
            "return": "enter",
            "pgup": "page_up",
            "pgdn": "page_down",
            "del": "delete",
            "win": "cmd",
            "windows": "cmd",
        }
        part = aliases.get(part, part)
        if part.startswith("<") and part.endswith(">"):
            part = part[1:-1]
        if part in SPECIAL_HOTKEY_NAMES:
            normalized.append(f"<{part}>")
        elif len(part) == 1:
            normalized.append(part)
        else:
            raise ValueError(f"Unsupported hotkey key: {part}")

    return "+".join(normalized)


def key_tracking_id(data: dict) -> str:
    return f"{data.get('kind')}:{data.get('value')}"


def hotkey_ignored_key_ids(hotkeys: list[str]) -> set[str]:
    ignored: set[str] = set()
    modifier_variants = {
        "alt": ("alt", "alt_l", "alt_r"),
        "cmd": ("cmd", "cmd_l", "cmd_r"),
        "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
        "shift": ("shift", "shift_l", "shift_r"),
    }

    for hotkey in hotkeys:
        for token in hotkey.split("+"):
            token = token.strip()
            if token.startswith("<") and token.endswith(">"):
                name = token[1:-1]
                for variant in modifier_variants.get(name, (name,)):
                    ignored.add(f"special:{variant}")
            elif len(token) == 1:
                ignored.add(f"char:{token}")

    return ignored


class MacroRecorderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        configure_windows_input_process()

        self.title("Macro Recorder")
        self.geometry("940x680")
        self.minsize(820, 600)

        self.events: list[MacroEvent] = []
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self.recording = False
        self.playing = False
        self.record_started_at = 0.0
        self.last_move_recorded_at = 0.0
        self.mouse_listener: object | None = None
        self.keyboard_listener: object | None = None
        self.hotkey_listener: object | None = None
        self.control_hotkey_key_ids: set[str] = set()
        self.playback_thread: threading.Thread | None = None
        self.stop_playback = threading.Event()

        self.speed_var = tk.DoubleVar(value=1.0)
        self.loop_delay_hours_var = tk.IntVar(value=0)
        self.loop_delay_minutes_var = tk.IntVar(value=0)
        self.loop_delay_seconds_var = tk.DoubleVar(value=1.0)
        self.loop_count_var = tk.IntVar(value=1)
        self.infinite_var = tk.BooleanVar(value=False)
        self.keep_awake_var = tk.BooleanVar(value=True)
        self.mouse_settle_ms_var = tk.DoubleVar(value=75.0)
        self.click_hold_ms_var = tk.DoubleVar(value=75.0)
        self.mouse_backend_var = tk.StringVar(value="Win32 mouse events (Roblox)")
        self.start_record_hotkey_var = tk.StringVar(value="F8")
        self.stop_record_hotkey_var = tk.StringVar(value="F9")
        self.start_play_hotkey_var = tk.StringVar(value="F10")
        self.stop_play_hotkey_var = tk.StringVar(value="F12")
        self.status_var = tk.StringVar(value="Ready")
        self.count_var = tk.StringVar(value="0 events")

        self._build_ui()
        self._set_idle_state()
        self.after(100, self._drain_messages)
        self._load_autosaved_macro()

        if keyboard is None or mouse is None:
            messagebox.showerror(
                "Missing dependency",
                "pynput is not installed.\n\nRun:\npip install -r requirements.txt",
            )
            self._log("Missing dependency: install pynput with pip install -r requirements.txt")
        else:
            self._apply_hotkeys(show_success=False)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="we", pady=(0, 12))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Macro Recorder", font=("Segoe UI", 20, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Record keyboard and mouse input, then replay it with custom speed and loop settings.",
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(header, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="e")
        ttk.Label(header, textvariable=self.count_var, foreground="#555555").grid(row=1, column=1, sticky="e", pady=(4, 0))

        controls = ttk.LabelFrame(root, text="Controls", padding=12)
        controls.grid(row=1, column=0, sticky="we", pady=(0, 12))
        for column in range(6):
            controls.columnconfigure(column, weight=1 if column in (1, 3, 5) else 0)

        self.record_button = ttk.Button(controls, text="Record", command=self._start_recording)
        self.record_button.grid(row=0, column=0, sticky="we", padx=(0, 8))
        self.stop_record_button = ttk.Button(controls, text="Stop Recording", command=lambda: self._stop_recording(trim_recent=True))
        self.stop_record_button.grid(row=0, column=1, sticky="we", padx=(0, 8))
        self.play_button = ttk.Button(controls, text="Play", command=self._start_playback)
        self.play_button.grid(row=0, column=2, sticky="we", padx=(0, 8))
        self.stop_play_button = ttk.Button(controls, text="Stop Playback", command=self._stop_playback)
        self.stop_play_button.grid(row=0, column=3, sticky="we", padx=(0, 8))
        ttk.Button(controls, text="Clear", command=self._clear_events).grid(row=0, column=4, sticky="we", padx=(0, 8))
        ttk.Button(controls, text="Save", command=self._save_macro).grid(row=0, column=5, sticky="we")

        ttk.Button(controls, text="Load", command=self._load_macro).grid(row=1, column=0, sticky="we", pady=(10, 0), padx=(0, 8))
        ttk.Label(controls, text="Speed").grid(row=1, column=1, sticky="e", pady=(10, 0), padx=(0, 8))
        self.speed = ttk.Spinbox(controls, from_=0.1, to=10.0, increment=0.1, textvariable=self.speed_var, width=8)
        self.speed.grid(row=1, column=2, sticky="w", pady=(10, 0), padx=(0, 8))
        ttk.Label(controls, text="Loop delay").grid(row=1, column=3, sticky="e", pady=(10, 0), padx=(0, 8))
        delay_frame = ttk.Frame(controls)
        delay_frame.grid(row=1, column=4, sticky="w", pady=(10, 0), padx=(0, 8))
        self.loop_delay_hours = ttk.Spinbox(
            delay_frame,
            from_=0,
            to=24,
            increment=1,
            textvariable=self.loop_delay_hours_var,
            width=4,
        )
        self.loop_delay_hours.pack(side="left")
        ttk.Label(delay_frame, text="h").pack(side="left", padx=(3, 8))
        self.loop_delay_minutes = ttk.Spinbox(
            delay_frame,
            from_=0,
            to=59,
            increment=1,
            textvariable=self.loop_delay_minutes_var,
            width=4,
        )
        self.loop_delay_minutes.pack(side="left")
        ttk.Label(delay_frame, text="m").pack(side="left", padx=(3, 8))
        self.loop_delay_seconds = ttk.Spinbox(
            delay_frame,
            from_=0.0,
            to=59.9,
            increment=0.5,
            textvariable=self.loop_delay_seconds_var,
            width=5,
        )
        self.loop_delay_seconds.pack(side="left")
        ttk.Label(delay_frame, text="s").pack(side="left", padx=(3, 0))

        loop_frame = ttk.Frame(controls)
        loop_frame.grid(row=1, column=5, sticky="e", pady=(10, 0))
        ttk.Label(loop_frame, text="Loops").pack(side="left", padx=(0, 6))
        self.loop_count = ttk.Spinbox(loop_frame, from_=1, to=1000000, increment=1, textvariable=self.loop_count_var, width=8)
        self.loop_count.pack(side="left")
        ttk.Checkbutton(loop_frame, text="Infinite", variable=self.infinite_var, command=self._set_loop_state).pack(
            side="left", padx=(10, 0)
        )
        ttk.Checkbutton(controls, text="Keep PC awake during playback", variable=self.keep_awake_var).grid(
            row=2, column=0, columnspan=6, sticky="w", pady=(10, 0)
        )
        ttk.Label(controls, text="Mouse settle (ms)").grid(row=3, column=0, sticky="e", pady=(10, 0), padx=(0, 8))
        self.mouse_settle = ttk.Spinbox(
            controls,
            from_=0,
            to=1000,
            increment=25,
            textvariable=self.mouse_settle_ms_var,
            width=8,
        )
        self.mouse_settle.grid(row=3, column=1, sticky="w", pady=(10, 0), padx=(0, 8))
        ttk.Label(controls, text="Click hold (ms)").grid(row=3, column=2, sticky="e", pady=(10, 0), padx=(0, 8))
        self.click_hold = ttk.Spinbox(
            controls,
            from_=0,
            to=1000,
            increment=25,
            textvariable=self.click_hold_ms_var,
            width=8,
        )
        self.click_hold.grid(row=3, column=3, sticky="w", pady=(10, 0), padx=(0, 8))
        ttk.Label(controls, text="Mouse backend").grid(row=3, column=4, sticky="e", pady=(10, 0), padx=(0, 8))
        self.mouse_backend = ttk.Combobox(
            controls,
            textvariable=self.mouse_backend_var,
            values=("Win32 mouse events (Roblox)", "pynput mouse controller"),
            state="readonly",
            width=24,
        )
        self.mouse_backend.grid(row=3, column=5, sticky="e", pady=(10, 0))

        hotkeys = ttk.LabelFrame(root, text="Hotkeys", padding=12)
        hotkeys.grid(row=2, column=0, sticky="we", pady=(0, 12))
        for column in range(8):
            hotkeys.columnconfigure(column, weight=1 if column % 2 else 0)

        ttk.Label(hotkeys, text="Start record").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(hotkeys, textvariable=self.start_record_hotkey_var, width=14).grid(
            row=0, column=1, sticky="we", padx=(0, 12), pady=(0, 8)
        )
        ttk.Label(hotkeys, text="Stop record").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(hotkeys, textvariable=self.stop_record_hotkey_var, width=14).grid(
            row=0, column=3, sticky="we", padx=(0, 12), pady=(0, 8)
        )
        ttk.Label(hotkeys, text="Start play").grid(row=0, column=4, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(hotkeys, textvariable=self.start_play_hotkey_var, width=14).grid(
            row=0, column=5, sticky="we", padx=(0, 12), pady=(0, 8)
        )
        ttk.Label(hotkeys, text="Stop play").grid(row=0, column=6, sticky="w", padx=(0, 8), pady=(0, 8))
        ttk.Entry(hotkeys, textvariable=self.stop_play_hotkey_var, width=14).grid(
            row=0, column=7, sticky="we", pady=(0, 8)
        )

        ttk.Label(
            hotkeys,
            text="Examples: F9, Ctrl+Alt+R, Shift+F6. Single-letter hotkeys work, but combinations are safer.",
            foreground="#555555",
        ).grid(row=1, column=0, columnspan=7, sticky="w")
        ttk.Button(hotkeys, text="Apply Hotkeys", command=lambda: self._apply_hotkeys(show_success=True)).grid(
            row=1, column=7, sticky="e"
        )

        tips = ttk.LabelFrame(root, text="Safety", padding=12)
        tips.grid(row=3, column=0, sticky="we", pady=(0, 12))
        ttk.Label(
            tips,
            text="Use your stop hotkeys or buttons to halt recording and playback. Keep playback speed modest until you trust the macro, because recorded input controls your real mouse and keyboard.",
            wraplength=860,
            foreground="#555555",
        ).pack(anchor="w")

        body = ttk.PanedWindow(root, orient="vertical")
        body.grid(row=4, column=0, sticky="nsew")

        event_frame = ttk.LabelFrame(body, text="Recorded Events", padding=8)
        event_frame.rowconfigure(0, weight=1)
        event_frame.columnconfigure(0, weight=1)
        self.events_view = ttk.Treeview(event_frame, columns=("time", "type", "details"), show="headings", height=12)
        self.events_view.heading("time", text="Time")
        self.events_view.heading("type", text="Type")
        self.events_view.heading("details", text="Details")
        self.events_view.column("time", width=90, anchor="e")
        self.events_view.column("type", width=130)
        self.events_view.column("details", width=650)
        self.events_view.grid(row=0, column=0, sticky="nsew")
        event_scrollbar = ttk.Scrollbar(event_frame, command=self.events_view.yview)
        event_scrollbar.grid(row=0, column=1, sticky="ns")
        self.events_view.configure(yscrollcommand=event_scrollbar.set)
        body.add(event_frame, weight=3)

        log_frame = ttk.LabelFrame(body, text="Log", padding=8)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=log_scrollbar.set)
        body.add(log_frame, weight=1)

    def _set_idle_state(self) -> None:
        has_events = bool(self.events)
        self.record_button.configure(state="normal" if not self.recording and not self.playing else "disabled")
        self.stop_record_button.configure(state="normal" if self.recording else "disabled")
        self.play_button.configure(state="normal" if has_events and not self.recording and not self.playing else "disabled")
        self.stop_play_button.configure(state="normal" if self.playing else "disabled")
        self._set_loop_state()

    def _set_loop_state(self) -> None:
        self.loop_count.configure(state="disabled" if self.infinite_var.get() else "normal")

    def _loop_delay_seconds(self) -> float:
        hours = int(self.loop_delay_hours_var.get())
        minutes = int(self.loop_delay_minutes_var.get())
        seconds = float(self.loop_delay_seconds_var.get())

        if hours < 0 or minutes < 0 or seconds < 0:
            raise ValueError("Loop delay cannot be negative.")
        if minutes > 59:
            raise ValueError("Loop delay minutes must be between 0 and 59.")
        if seconds >= 60:
            raise ValueError("Loop delay seconds must be less than 60.")

        total = hours * 3600 + minutes * 60 + seconds
        if total > MAX_LOOP_DELAY_SECONDS:
            raise ValueError("Loop delay cannot be longer than 24 hours.")
        return total

    def _mouse_playback_timing(self) -> tuple[float, float]:
        mouse_settle = float(self.mouse_settle_ms_var.get()) / 1000
        click_hold = float(self.click_hold_ms_var.get()) / 1000
        if mouse_settle < 0 or click_hold < 0:
            raise ValueError("Mouse settle and click hold cannot be negative.")
        if mouse_settle > 1 or click_hold > 1:
            raise ValueError("Mouse settle and click hold cannot be longer than 1000 ms.")
        return mouse_settle, click_hold

    def _use_win32_mouse_backend(self) -> bool:
        return self.mouse_backend_var.get().startswith("Win32")

    @staticmethod
    def _format_duration(total_seconds: float) -> str:
        hours, remainder = divmod(int(total_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        parts = []
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if seconds or not parts:
            parts.append(f"{seconds}s")
        return " ".join(parts)

    def _set_keep_awake(self, enabled: bool) -> None:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED if enabled else ES_CONTINUOUS
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        except Exception:
            pass

    def _apply_hotkeys(self, show_success: bool) -> None:
        if keyboard is None:
            return

        try:
            start_record_hotkey = normalize_hotkey(self.start_record_hotkey_var.get())
            stop_record_hotkey = normalize_hotkey(self.stop_record_hotkey_var.get())
            start_play_hotkey = normalize_hotkey(self.start_play_hotkey_var.get())
            stop_play_hotkey = normalize_hotkey(self.stop_play_hotkey_var.get())
            hotkeys = {
                start_record_hotkey: lambda: self.messages.put(("start_recording", None)),
                stop_record_hotkey: lambda: self.messages.put(("stop_recording", True)),
                start_play_hotkey: lambda: self.messages.put(("start_playback", None)),
                stop_play_hotkey: lambda: self.messages.put(("stop_playback", None)),
            }
            if len(hotkeys) != 4:
                raise ValueError("Each hotkey must be unique.")
            for hotkey in hotkeys:
                keyboard.HotKey.parse(hotkey)
        except Exception as exc:
            messagebox.showwarning("Invalid hotkey", str(exc))
            return

        self._stop_listener(self.hotkey_listener)
        self.control_hotkey_key_ids = hotkey_ignored_key_ids(list(hotkeys))
        self.hotkey_listener = keyboard.GlobalHotKeys(hotkeys)
        self.hotkey_listener.start()
        if show_success:
            self._log("Hotkeys updated.")

    def _start_recording(self) -> None:
        if keyboard is None or mouse is None:
            messagebox.showerror("Missing dependency", "Run: pip install -r requirements.txt")
            return
        if self.recording or self.playing:
            return

        self.events.clear()
        self._refresh_event_view()
        self.recording = True
        self.record_started_at = time.perf_counter()
        self.last_move_recorded_at = 0.0
        self.status_var.set("Recording")
        self._log("Recording started.")

        self.mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )
        self.keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self.mouse_listener.start()
        self.keyboard_listener.start()
        self._set_idle_state()

    def _stop_recording(self, trim_recent: bool = False) -> None:
        if not self.recording:
            return
        self.recording = False
        trim_after = self._elapsed() - 0.3
        self._stop_listener(self.mouse_listener)
        self._stop_listener(self.keyboard_listener)
        self.mouse_listener = None
        self.keyboard_listener = None
        if trim_recent and trim_after > 0:
            original_count = len(self.events)
            self.events = [event for event in self.events if event.time < trim_after]
            if len(self.events) != original_count:
                self._refresh_event_view()
        self.status_var.set("Ready")
        self._log(f"Recording stopped. Captured {len(self.events)} events.")
        self._autosave_macro()
        self._set_idle_state()

    @staticmethod
    def _stop_listener(listener: object | None) -> None:
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def _elapsed(self) -> float:
        return time.perf_counter() - self.record_started_at

    def _record_event(self, event_type: str, data: dict) -> None:
        if not self.recording:
            return
        if len(self.events) >= MAX_RECORDED_EVENTS:
            self.messages.put(("log", f"Recording stopped at {MAX_RECORDED_EVENTS} events to keep the app stable."))
            self.messages.put(("stop_recording", False))
            return
        event = MacroEvent(time=self._elapsed(), type=event_type, data=data)
        self.events.append(event)
        self.messages.put(("event", event))

    def _on_mouse_move(self, x: int, y: int) -> None:
        now = self._elapsed()
        if now - self.last_move_recorded_at < 0.02:
            return
        self.last_move_recorded_at = now
        self._record_event("mouse_move", {"x": x, "y": y})

    def _on_mouse_click(self, x: int, y: int, button: object, pressed: bool) -> None:
        self._record_event(
            "mouse_click",
            {"x": x, "y": y, "button": serialize_button(button), "pressed": pressed},
        )

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        self._record_event("mouse_scroll", {"x": x, "y": y, "dx": dx, "dy": dy})

    def _on_key_press(self, key: object) -> None:
        key_data = serialize_key(key)
        if key_tracking_id(key_data) in self.control_hotkey_key_ids:
            return
        self._record_event("key_press", {"key": key_data})

    def _on_key_release(self, key: object) -> None:
        key_data = serialize_key(key)
        if key_tracking_id(key_data) in self.control_hotkey_key_ids:
            return
        self._record_event("key_release", {"key": key_data})

    def _start_playback(self) -> None:
        if keyboard is None or mouse is None:
            messagebox.showerror("Missing dependency", "Run: pip install -r requirements.txt")
            return
        if self.recording or self.playing or not self.events:
            return

        try:
            speed = self._positive_float(self.speed_var.get(), "Playback speed")
            loop_delay = self._loop_delay_seconds()
            mouse_settle, click_hold = self._mouse_playback_timing()
            use_win32_mouse = self._use_win32_mouse_backend()
            loop_count = max(1, int(self.loop_count_var.get()))
        except (TypeError, ValueError) as exc:
            messagebox.showwarning("Invalid playback settings", str(exc))
            return

        self.playing = True
        self.stop_playback.clear()
        self.status_var.set("Playing")
        delay_text = self._format_duration(loop_delay)
        loops_text = "infinite loops" if self.infinite_var.get() else f"{loop_count} loop(s)"
        self._log(
            f"Playback started: {loops_text}, {speed:g}x speed, {delay_text} loop delay, "
            f"{mouse_settle * 1000:g} ms mouse settle, {click_hold * 1000:g} ms click hold, "
            f"{'Win32 mouse backend' if use_win32_mouse else 'pynput mouse backend'}."
        )
        if self.keep_awake_var.get():
            self._set_keep_awake(True)
            self._log("Windows sleep prevention is active during playback.")
        self.playback_thread = threading.Thread(
            target=self._playback,
            args=(speed, loop_delay, loop_count, self.infinite_var.get(), mouse_settle, click_hold, use_win32_mouse),
            daemon=True,
        )
        self.playback_thread.start()
        self._set_idle_state()

    @staticmethod
    def _positive_float(value: float, label: str) -> float:
        number = float(value)
        if number <= 0:
            raise ValueError(f"{label} must be greater than zero.")
        return number

    def _stop_playback(self) -> None:
        if self.playing:
            self.stop_playback.set()
            self.status_var.set("Stopping...")
            self._log("Stop requested.")

    def _playback(
        self,
        speed: float,
        loop_delay: float,
        loop_count: int,
        infinite: bool,
        mouse_settle: float,
        click_hold: float,
        use_win32_mouse: bool,
    ) -> None:
        mouse_controller = mouse.Controller()
        keyboard_controller = keyboard.Controller()
        pressed_keys: dict[str, object] = {}
        pressed_buttons: dict[str, object] = {}
        pressed_button_times: dict[str, float] = {}
        iteration = 0

        try:
            while not self.stop_playback.is_set() and (infinite or iteration < loop_count):
                iteration += 1
                self.messages.put(("status", f"Playing loop {iteration}" if infinite else f"Playing loop {iteration}/{loop_count}"))
                previous_time = 0.0
                for event in list(self.events):
                    if self.stop_playback.is_set():
                        break
                    delay = max(0.0, event.time - previous_time) / speed
                    previous_time = event.time
                    if self.stop_playback.wait(delay):
                        break
                    self._play_event(
                        event,
                        mouse_controller,
                        keyboard_controller,
                        pressed_keys,
                        pressed_buttons,
                        pressed_button_times,
                        mouse_settle,
                        click_hold,
                        use_win32_mouse,
                    )

                if self.stop_playback.is_set() or (not infinite and iteration >= loop_count):
                    break
                self.messages.put(("status", f"Waiting {self._format_duration(loop_delay)}"))
                if self.stop_playback.wait(loop_delay):
                    break

            self.messages.put(("log", "Playback stopped." if self.stop_playback.is_set() else "Playback finished."))
        except Exception as exc:
            self.messages.put(("log", f"Playback error: {exc}"))
            self.messages.put(("error", str(exc)))
        finally:
            self._release_pressed_inputs(keyboard_controller, mouse_controller, pressed_keys, pressed_buttons)
            self._set_keep_awake(False)
            self.messages.put(("playback_done", None))

    def _play_event(
        self,
        event: MacroEvent,
        mouse_controller: object,
        keyboard_controller: object,
        pressed_keys: dict[str, object],
        pressed_buttons: dict[str, object],
        pressed_button_times: dict[str, float],
        mouse_settle: float,
        click_hold: float,
        use_win32_mouse: bool,
    ) -> None:
        data = event.data
        if event.type == "mouse_move":
            self._move_mouse(mouse_controller, data["x"], data["y"], use_win32_mouse, pulse=use_win32_mouse)
        elif event.type == "mouse_click":
            button = deserialize_button(data["button"])
            if button is None and not use_win32_mouse:
                return
            self._move_mouse(mouse_controller, data["x"], data["y"], use_win32_mouse, pulse=True)
            if data["pressed"]:
                if mouse_settle and self.stop_playback.wait(mouse_settle):
                    return
                self._set_mouse_button(mouse_controller, data["button"], button, True, use_win32_mouse)
                pressed_buttons[data["button"]] = button if button is not None else data["button"]
                pressed_button_times[data["button"]] = time.perf_counter()
            else:
                pressed_at = pressed_button_times.get(data["button"])
                if pressed_at is not None:
                    remaining = click_hold - (time.perf_counter() - pressed_at)
                    if remaining > 0 and self.stop_playback.wait(remaining):
                        return
                self._set_mouse_button(mouse_controller, data["button"], button, False, use_win32_mouse)
                pressed_buttons.pop(data["button"], None)
                pressed_button_times.pop(data["button"], None)
        elif event.type == "mouse_scroll":
            self._move_mouse(mouse_controller, data["x"], data["y"], use_win32_mouse, pulse=use_win32_mouse)
            if use_win32_mouse:
                win32_mouse_scroll(data["dy"])
            else:
                mouse_controller.scroll(data["dx"], data["dy"])
        elif event.type == "key_press":
            key = deserialize_key(data["key"])
            if key is not None:
                keyboard_controller.press(key)
                pressed_keys[self._key_tracking_id(data["key"])] = key
        elif event.type == "key_release":
            key = deserialize_key(data["key"])
            if key is not None:
                keyboard_controller.release(key)
                pressed_keys.pop(self._key_tracking_id(data["key"]), None)

    @staticmethod
    def _key_tracking_id(data: dict) -> str:
        return key_tracking_id(data)

    @staticmethod
    def _move_mouse(mouse_controller: object, x: int, y: int, use_win32_mouse: bool, pulse: bool = False) -> None:
        if use_win32_mouse:
            win32_move_mouse(x, y, pulse=pulse)
        else:
            mouse_controller.position = (x, y)

    @staticmethod
    def _set_mouse_button(
        mouse_controller: object,
        button_name: str,
        button: object | None,
        pressed: bool,
        use_win32_mouse: bool,
    ) -> None:
        if use_win32_mouse:
            win32_mouse_button(button_name, pressed)
        elif button is not None:
            if pressed:
                mouse_controller.press(button)
            else:
                mouse_controller.release(button)

    def _release_pressed_inputs(
        self,
        keyboard_controller: object,
        mouse_controller: object,
        pressed_keys: dict[str, object],
        pressed_buttons: dict[str, object],
    ) -> None:
        for key in list(pressed_keys.values()):
            try:
                keyboard_controller.release(key)
            except Exception:
                pass
        pressed_keys.clear()

        for button in list(pressed_buttons.values()):
            try:
                if isinstance(button, str):
                    win32_mouse_button(button, False)
                else:
                    mouse_controller.release(button)
            except Exception:
                pass
        pressed_buttons.clear()

    def _clear_events(self) -> None:
        if self.recording or self.playing:
            return
        self.events.clear()
        self._refresh_event_view()
        self._autosave_macro()
        self._log("Macro cleared.")
        self._set_idle_state()

    def _save_macro(self) -> None:
        if not self.events:
            messagebox.showwarning("Nothing to save", "Record or load a macro first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save macro",
            defaultextension=".json",
            filetypes=(("Macro JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        Path(path).write_text(json.dumps(macro_payload(self.events), indent=2), encoding="utf-8")
        self._log(f"Saved macro: {path}")

    def _load_macro(self) -> None:
        if self.recording or self.playing:
            return
        path = filedialog.askopenfilename(
            title="Load macro",
            filetypes=(("Macro JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.events = events_from_payload(payload)
        except Exception as exc:
            messagebox.showerror("Could not load macro", str(exc))
            return
        self._refresh_event_view()
        self._autosave_macro()
        self._log(f"Loaded macro: {path}")
        self._set_idle_state()

    def _autosave_macro(self) -> None:
        try:
            MACROS_DIR.mkdir(exist_ok=True)
            AUTOSAVE_MACRO_PATH.write_text(json.dumps(macro_payload(self.events), indent=2), encoding="utf-8")
        except Exception as exc:
            self._log(f"Could not autosave macro: {exc}")

    def _load_autosaved_macro(self) -> None:
        if not AUTOSAVE_MACRO_PATH.exists():
            return
        try:
            payload = json.loads(AUTOSAVE_MACRO_PATH.read_text(encoding="utf-8"))
            self.events = events_from_payload(payload)
        except Exception as exc:
            self._log(f"Could not restore last macro: {exc}")
            return
        self._refresh_event_view()
        self._set_idle_state()
        if self.events:
            self._log(f"Restored last macro from {AUTOSAVE_MACRO_PATH}.")

    def _drain_messages(self) -> None:
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break

            if kind == "event":
                self._append_event(value)
            elif kind == "log":
                self._log(str(value))
            elif kind == "status":
                self.status_var.set(str(value))
            elif kind == "error":
                messagebox.showerror("Playback error", str(value))
            elif kind == "start_recording":
                self._start_recording()
            elif kind == "stop_recording":
                self._stop_recording(trim_recent=bool(value))
            elif kind == "start_playback":
                self._start_playback()
            elif kind == "stop_playback":
                self._stop_playback()
            elif kind == "playback_done":
                self.playing = False
                self.stop_playback.clear()
                self.status_var.set("Ready")
                self._set_idle_state()

        self.after(100, self._drain_messages)

    def _append_event(self, event: object) -> None:
        if not isinstance(event, MacroEvent):
            return
        self.events_view.insert("", "end", values=(f"{event.time:.3f}s", event.type, self._event_details(event)))
        self.events_view.see(self.events_view.get_children()[-1])
        self.count_var.set(f"{len(self.events)} events")
        self._set_idle_state()

    def _refresh_event_view(self) -> None:
        for item in self.events_view.get_children():
            self.events_view.delete(item)
        for event in self.events:
            self.events_view.insert("", "end", values=(f"{event.time:.3f}s", event.type, self._event_details(event)))
        self.count_var.set(f"{len(self.events)} events")

    @staticmethod
    def _event_details(event: MacroEvent) -> str:
        data = event.data
        if event.type == "mouse_move":
            return f"x={data['x']}, y={data['y']}"
        if event.type == "mouse_click":
            state = "down" if data["pressed"] else "up"
            return f"{data['button']} {state} at x={data['x']}, y={data['y']}"
        if event.type == "mouse_scroll":
            return f"dx={data['dx']}, dy={data['dy']} at x={data['x']}, y={data['y']}"
        if event.type in {"key_press", "key_release"}:
            key_data = data["key"]
            return f"{key_data.get('kind')}:{key_data.get('value')}"
        return json.dumps(data)

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def destroy(self) -> None:
        self._stop_recording()
        self._stop_playback()
        self._stop_listener(self.hotkey_listener)
        super().destroy()


if __name__ == "__main__":
    app = MacroRecorderApp()
    app.mainloop()
