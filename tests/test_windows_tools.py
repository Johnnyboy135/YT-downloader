import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app


class BundledToolsTests(unittest.TestCase):
    def test_installed_tools_are_found_without_path(self):
        root = Path.cwd() / "example-install"
        tools = root / "_internal/tools"
        with (
            patch.object(Path, "is_file", autospec=True,
                         side_effect=lambda p: p in {tools / "ffmpeg.exe", tools / "deno.exe"}),
            patch.object(app, "__file__", str(root / "_internal/app.py")),
            patch.object(app.sys, "frozen", True, create=True),
            patch.object(app.sys, "executable", str(root / "YouTube Downloader.exe")),
            patch.object(app.shutil, "which", return_value=None),
        ):
            self.assertEqual(app.find_ffmpeg(), tools / "ffmpeg.exe")
            self.assertEqual(app.bundled_tool("deno.exe"), tools / "deno.exe")

    def test_external_ffmpeg_remains_supported(self):
        with patch.object(app, "bundled_tool", return_value=None), \
             patch.object(app.shutil, "which", return_value="C:/ffmpeg/bin/ffmpeg.exe"):
            self.assertEqual(app.find_ffmpeg(), Path("C:/ffmpeg/bin/ffmpeg.exe"))

    def test_absent_bundled_tool_returns_none(self):
        with patch.object(Path, "is_file", return_value=False), \
             patch.object(app.sys, "frozen", False, create=True):
            self.assertIsNone(app.bundled_tool("deno.exe"))

    def test_download_options_use_bundled_runtimes(self):
        root = Path.cwd() / "example-install/_internal/tools"
        state = SimpleNamespace(
            ffmpeg_path=root / "ffmpeg.exe",
            mode_var=SimpleNamespace(get=lambda: "video"),
            video_quality_var=SimpleNamespace(get=lambda: "1080p"),
            _progress_hook=lambda data: None,
        )
        with patch.object(app, "bundled_tool", return_value=root / "deno.exe"):
            options = app.DownloadApp._yt_dlp_options(state, Path.cwd())
        self.assertEqual(options["ffmpeg_location"], str(root))
        self.assertEqual(options["js_runtimes"], {"deno": {"path": str(root / "deno.exe")}})
        self.assertIn("height<=1080", options["format"])


if __name__ == "__main__":
    unittest.main()
