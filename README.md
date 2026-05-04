# YouTube Downloader

A small local desktop app for downloading YouTube audio or video when you own the content, it is licensed for download, or you otherwise have permission.

It uses `yt-dlp` for downloads and `ffmpeg` for audio extraction, conversion, and some video merges.

## Setup

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

## Run

```powershell
python app.py
```

## Notes

- This app does not include cookie import, DRM circumvention, login automation, or restriction bypass features.
- Some downloads require `ffmpeg` because YouTube often serves audio and video as separate streams.
- By default the app downloads one video, not an entire playlist.
