# Macro Recorder

A small local desktop app for recording keyboard input, mouse movement, mouse clicks, and scrolling, then replaying the macro.

## Features

- Record global keyboard and mouse events.
- Replay at a custom speed from `0.1x` to `10x`.
- Add an hours/minutes/seconds delay after each loop.
- Run a fixed number of loops or loop forever.
- Save and load macros as JSON files.
- Automatically restores your last macro when you close and reopen the app.
- Customize the global hotkeys for start/stop recording and start/stop playback.
- Tune mouse playback with a Roblox-oriented Win32 mouse backend, mouse-settle timing, and click-hold timing.

## Setup

1. Install Python 3.11 or newer.
2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

## Run

```powershell
python app.py
```

Or run the helper script:

```powershell
.\run.ps1
```

## Notes

- Playback controls your real mouse and keyboard. Test new macros with a low speed and a small loop count first.
- The last recorded or loaded macro is autosaved to `macros/last_macro.json`.
- Default hotkeys are `F8` to start recording, `F9` to stop recording, `F10` to start playback, and `F12` to stop playback.
- Hotkeys can use single keys like `F9` or combinations like `Ctrl+Alt+R`.
- Control hotkey key presses/releases are filtered out while recording, so start/stop hotkeys do not appear in the macro.
- The app caps recordings at 50,000 events to avoid runaway memory use.
- Playback releases any keys or mouse buttons it pressed when playback stops or errors.
- For Roblox, keep `Mouse backend` set to `Win32 mouse events (Roblox)`.
- If Roblox still misses clicks after mouse movement, increase `Mouse settle (ms)` and `Click hold (ms)`.
- If Roblox is running as administrator, run this recorder as administrator too; Windows can block lower-privilege apps from sending input to elevated apps.
- Loop delays are interruptible, so the stop playback hotkey still works during long overnight waits.
- `Keep PC awake during playback` asks Windows not to sleep while a macro is running.
- Some applications with elevated/admin privileges may ignore input from a non-elevated recorder.
- On Windows, global input hooks may be blocked by security software or organization policy.
