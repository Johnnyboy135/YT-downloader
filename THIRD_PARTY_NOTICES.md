# Third-party components

The Windows installer bundles Python, Tcl/Tk, yt-dlp and its dependencies,
FFmpeg/ffprobe, and Deno. Their license notices are included in the installed
`_internal/licenses` folder. PyInstaller is used under its bootloader exception.

- Python: https://www.python.org/ (PSF license)
- Tcl/Tk: https://www.tcl.tk/ (BSD-style license)
- yt-dlp: https://github.com/yt-dlp/yt-dlp (Unlicense; dependencies have their own licenses)
- yt-dlp-ejs: https://github.com/yt-dlp/ejs
- Deno: https://github.com/denoland/deno (MIT; third-party notices in its source tree)
- FFmpeg 9.0.2: https://www.gyan.dev/ffmpeg/builds/ (GPLv3 build)
  - FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/n9.0.2
  - Build information and external library versions: bundled ffmpeg README and
    https://www.gyan.dev/ffmpeg/builds/#libraries

The exact binary download URLs and SHA-256 checksums are recorded in
`packaging/windows-dependencies.json`, also included with the installed notices.
