# YouTube Downloader

A small local desktop app for downloading YouTube audio or video when you own the content, it is licensed for download, or you otherwise have permission.

It uses `yt-dlp` for downloads and `ffmpeg` for audio extraction, conversion, and some video merges.

## Windows installer

Windows 10/11 (64-bit) users can run `YT-Downloader-Setup-<version>-x64.exe`.
Python, yt-dlp, FFmpeg/ffprobe, and Deno are included; no terminal setup or
administrator access is needed. Setup adds a Start menu entry, offers a desktop
shortcut, and includes an uninstaller in Windows **Installed apps**.

To pin it, open **YouTube Downloader**, right-click its taskbar icon, and choose
**Pin to taskbar**. Windows keeps this choice under the user's control.

Installers are built by the [Windows installer workflow](https://github.com/Johnnyboy135/YT-downloader/actions/workflows/windows-installer.yml).
Download the `Windows-installer-x64` artifact from a successful run and unzip it.
GitHub requires sign-in to download workflow artifacts. Tagged versions are also
uploaded to [Releases](https://github.com/Johnnyboy135/YT-downloader/releases).

The installer is currently unsigned; no signing certificate is included in this repository.

## Run from source

1. Install Python 3.11 or newer.
2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Install `ffmpeg` if you want audio-only downloads, MP3/WAV conversion, or fixed-resolution video downloads.
   The app checks your PATH and a few common local install locations, including `C:\Users\<you>\.stacher\ffmpeg.exe`.

   With Winget on Windows:

   ```powershell
   winget install Gyan.FFmpeg
   ```

4. Install [Deno](https://docs.deno.com/runtime/getting_started/installation/) 2.3+
   and make it available on PATH for yt-dlp's YouTube JavaScript support.

## Run

```powershell
python app.py
```

## Build the Windows installer

On 64-bit Windows, install Python 3.12 and [Inno Setup](https://jrsoftware.org/isdl.php)
6.3 or later, then run:

```powershell
python -m venv .venv-build
.\build-windows.ps1 -Python .\.venv-build\Scripts\python.exe
```

Pass `-IsccPath "C:\path\to\ISCC.exe"` if the compiler is installed elsewhere.
The build runs unit tests, downloads checksum-verified tools from the pinned
manifest, creates a windowed PyInstaller app, and writes the setup executable to
`installer-output/`. It does not install the app on the build machine.

Edit `VERSION` before a release. Push a matching tag, such as `v1.0.0`, to build
and upload its installer to GitHub Releases. Normal pushes and pull requests
produce downloadable workflow artifacts. Use the same installer to upgrade.

Bundled tool versions/checksums live in `packaging/windows-dependencies.json`.
Rebuild to update yt-dlp and dependencies; installed copies do not auto-update.
See `THIRD_PARTY_NOTICES.md` and the installed `_internal/licenses` directory.

## Notes

- This app does not include cookie import, DRM circumvention, login automation, or restriction bypass features.
- Some downloads require `ffmpeg` because YouTube often serves audio and video as separate streams.
- By default the app downloads one video, not an entire playlist.
