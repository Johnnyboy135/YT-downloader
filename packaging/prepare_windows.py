"""Fetch pinned media tools and collect notices for the Windows bundle."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "build" / "vendor"


def main():
    manifest = json.loads((ROOT / "packaging/windows-dependencies.json").read_text())
    tools = VENDOR / "tools"
    notices = VENDOR / "runtime-licenses"
    tools.mkdir(parents=True, exist_ok=True)
    notices.mkdir(parents=True, exist_ok=True)
    for name, dependency in manifest.items():
        archive = VENDOR / f"{name}-{dependency['version']}.zip"
        if not archive.exists():
            print(f"Downloading {name} {dependency['version']}...", flush=True)
            with urllib.request.urlopen(dependency["url"], timeout=120) as response:
                with archive.open("wb") as output:
                    shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != dependency["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch: {archive}. Remove it and retry.")
        expected = {"ffmpeg": {"ffmpeg.exe", "ffprobe.exe"}, "deno": {"deno.exe"}}[name]
        found = set()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                filename = Path(member.filename).name
                if filename in expected:
                    (tools / filename).write_bytes(package.read(member))
                    found.add(filename)
                elif filename.lower().startswith(("license", "copying", "readme")):
                    (notices / f"{name}-{filename}").write_bytes(package.read(member))
        if found != expected:
            raise RuntimeError(f"Missing binaries in {archive}: {expected - found}")

    for distribution in importlib.metadata.distributions():
        for item in distribution.files or []:
            if item.name.lower().startswith(("license", "copying")):
                source = distribution.locate_file(item)
                if source.is_file():
                    target = notices / distribution.metadata["Name"] / str(item)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    shutil.copy2(ROOT / "packaging/windows-dependencies.json", notices / "windows-dependencies.json")
    python_home = Path(sys.base_prefix)
    for name in ("LICENSE.txt", "LICENSE"):
        if (python_home / name).is_file():
            shutil.copy2(python_home / name, notices / "Python-LICENSE.txt")
    for source in (python_home / "tcl").glob("*/license.terms"):
        shutil.copy2(source, notices / f"{source.parent.name}-license.terms")
    deno_license = "https://raw.githubusercontent.com/denoland/deno/v" + manifest["deno"]["version"] + "/LICENSE.md"
    with urllib.request.urlopen(deno_license, timeout=30) as response:
        (notices / "Deno-LICENSE.md").write_bytes(response.read())
    print("Verified Windows dependencies and collected licenses.")


if __name__ == "__main__":
    main()
