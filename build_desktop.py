#!/usr/bin/env python3
"""Build the Qt desktop app on Windows or macOS using the active Python environment."""
import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
NAME = "Send-to-MASSO Manager"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onefile", action="store_true", help="Windows only: produce a single .exe instead of a folder")
    parser.add_argument("--dry-run", action="store_true", help="Print the build command without running it")
    args = parser.parse_args()
    if sys.platform not in ("win32", "darwin"):
        parser.error("Run this build on Windows for an .exe or on macOS for an .app.")
    if args.onefile and sys.platform != "win32":
        parser.error("Use the default folder-based .app bundle on macOS; --onefile is for Windows.")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
               "--onefile" if args.onefile else "--onedir", "--name", NAME,
               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
               "--specpath", str(ROOT / "build"), "--noupx", "--exclude-module", "tkinter",
               "--icon", str(ROOT / "S2M.ico"), "--hidden-import", "qrcode.image.pil"]
    for filename in ("S2M.ico", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        command.extend(["--add-data", f"{ROOT / filename}:."])
    command.extend(["--add-data", f"{ROOT / 'assets' / 'send-2-masso-logo.png'}:assets"])
    for package in ("PySide6-Essentials", "shiboken6", "qrcode", "Pillow"):
        command.extend(["--copy-metadata", package])
    if sys.platform == "darwin":
        command.extend(["--osx-bundle-identifier", "com.redmonkeymakers.sendtomasso"])
    command.append(str(ROOT / "send_to_masso_v2.py"))
    print(subprocess.list2cmdline(command) if sys.platform == "win32" else shlex.join(command), flush=True)
    if args.dry_run:
        return 0
    if importlib.util.find_spec("PyInstaller") is None:
        parser.error("Install build dependencies first: python -m pip install -r requirements-build.txt")
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    result = subprocess.run(command, cwd=ROOT, env=environment)
    if result.returncode:
        return result.returncode
    artifact = ROOT / "dist" / (f"{NAME}.app" if sys.platform == "darwin" else
                                f"{NAME}.exe" if args.onefile else NAME)
    print(f"\nBuilt: {artifact}")
    if sys.platform == "win32" and not args.onefile:
        print("Distribute the entire output folder, including _internal; the .exe depends on those files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
