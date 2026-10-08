# Building desktop bundles

The Qt v2 app can be packaged with PyInstaller. Users of the packaged application do not need Python or Tkinter installed.

Build Windows artifacts on Windows and macOS artifacts on macOS. PyInstaller packages the current interpreter and libraries; it does not cross-compile between these operating systems. See the [PyInstaller documentation](https://pyinstaller.org/en/stable/usage.html).

## macOS `.app`

From the project folder:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python build_desktop.py
open "dist/Send-to-MASSO Manager.app"
```

The output is `dist/Send-to-MASSO Manager.app`. Copy the `.app` to Applications, or send the complete application bundle. To create a ZIP that preserves its bundle structure and symbolic links:

```sh
ditto -c -k --sequesterRsrc --keepParent \
  "dist/Send-to-MASSO Manager.app" \
  "dist/Send-to-MASSO-Manager-macOS.zip"
```

Build with an Apple Silicon Python environment for an arm64 application or an Intel Python environment for an x86_64 application. This script uses the build interpreter's architecture. Universal builds require a universal Python environment and universal binary dependencies; this script does not promise a universal artifact.

A packaged Mac app saves settings at:

```text
~/Library/Application Support/Send-to-MASSO Manager/send_to_masso.json
```

Source runs continue using the project-local `send_to_masso.json`. To reuse source profiles with the packaged Mac app, copy that file to the location above after creating the directory. Keep settings outside the application bundle so replacing the app preserves them.

The build script does not configure a Developer ID identity or notarization. For distribution to other Mac users with normal Gatekeeper handling, sign and notarize the release using your Apple Developer credentials. See [PyInstaller's macOS signing guidance](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).

## Windows `.exe`

On a Windows machine, open PowerShell in the project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe build_desktop.py
```

The executable is:

```text
dist\Send-to-MASSO Manager\Send-to-MASSO Manager.exe
```

Distribute the **entire** `dist\Send-to-MASSO Manager` folder, including `_internal`, rather than copying just its executable. Create a ZIP with:

```powershell
Compress-Archive -Path "dist\Send-to-MASSO Manager" -DestinationPath "dist\Send-to-MASSO-Manager-Windows.zip" -Force
```

For a single standalone `.exe`, use:

```powershell
.\.venv\Scripts\python.exe build_desktop.py --onefile
```

This writes `dist\Send-to-MASSO Manager.exe`. One-file builds extract their dependencies at launch; use the folder build first when troubleshooting startup issues.

Windows bundles save `send_to_masso.json` beside the executable. Extract the application to a writable folder, as with the existing Windows release.

## Build verification

Run the automated tests in the same environment before building:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Use `.\.venv\Scripts\python.exe` instead on Windows.

After building, launch the artifact with `--demo` to inspect the UI without opening controller connections or writing settings. Check QR export and tool export as well as startup; controller uploads need separate real-hardware testing.

For a headless macOS smoke test:

```sh
QT_QPA_PLATFORM=offscreen \
  "dist/Send-to-MASSO Manager.app/Contents/MacOS/Send-to-MASSO Manager" \
  --screenshot /tmp/masso-packaged.png
```

The script bundles the app icon, project notices, dependency metadata, and the QR/Pillow image backend. Review the dependencies' distribution terms and include required Qt and other third-party license materials before publishing a release.
