# Changelog

Version 2 is an unreleased desktop preview. Earlier entries describe the retired v1 development line. Notes focus on user-visible behavior, protocol fixes, and distribution changes.

## v2 Qt desktop preview — unreleased

### Desktop and workflows

- Replaced the Tkinter interface with a PySide6 workspace containing File queue, Tools & export, and Profiles views, plus a collapsible Activity log.
- Added drag-and-drop file input, queue ordering, live machine status, and target-path previews. Normal startup begins with an empty queue.
- Added `--demo` with sample queue entries and disabled controller connections, uploads, and settings writes; added `--screenshot` for preview capture.
- Kept QR PNG export for selected entries or the whole queue, with overwrite confirmation and checks for duplicate output names.
- Separated tool-data download from text-file export so users choose when and where to save the result.
- Preserved existing profile/settings compatibility and the v1 protocol corrections in the extracted `masso_core.py` module.
- Removed in-app file merging and its supporting code and tests. Combined programs can be posted in Fusion and added as ordinary queue files.

### Reliability fixes

- Added independent queue orchestration that checks upload readiness before each file, requires status received within five seconds, and stops on failure or loss of readiness.
- Filtered incoming packets by the resolved address of the connected controller, pinned upload sockets to that address, and rejected packets from old listeners after reconnecting.
- Locked profile switching and editing while connected, and blocked additions and reordering during active queue runs.
- Preserved multiple selected rows across table refreshes.
- Kept pending and failed destinations synced to target-folder edits while preserving sending and completed destinations.

### Packaging and cleanup

- Replaced the legacy FLR icon with the Send-2-MASSO cutter-and-arrow emblem, with multiple sizes for desktop use.

- Added `requirements.txt`, `requirements-build.txt`, and a native PyInstaller build helper for Windows `.exe` and macOS `.app` artifacts, with a Windows `--onefile` option.
- Included the app icon, project license/notices, dependency metadata, and QR image backend in builds.
- Stored packaged Mac settings in `~/Library/Application Support/Send-to-MASSO Manager/`; source and portable Windows runs retain settings beside the program.
- Retired the v1.8.20 RC launcher and its logo asset; previous versions remain recoverable from Git history.
- Removed leftover merge resources and generated caches, and ignored macOS `.DS_Store` files.
- Updated the README, build/testing guides, interface preview, and third-party notices for v2.

### Validation

- All 31 automated protocol, connection-identity, queue, and desktop tests pass on macOS.
- Built and smoke-tested the Apple Silicon Mac app and verified its ad-hoc code signature.
- Real-controller verification of v2 and Windows testing remain outstanding; the Mac build has not been Developer ID signed or notarized for release distribution.

## v1.8.20 RC — historical

- Corrected file-data ACK decoding to use bytes 6-7 as a little-endian 16-bit next-expected index.
- Removed the v1.8.18 modulo-256 ACK workaround. It restored large-file uploads, but later captures showed the apparent rollover was caused by reading the wrong byte offset/order.
- Corrected compact final-packet padding so the payload after the CRC is a multiple of four bytes.
- The old odd/even trailer rule was incomplete; final lengths congruent to 3 modulo 4 (including the historical 411-byte and 180,499-byte cases) were padded incorrectly.
- Retained the full-wire-real-length final-packet fallback as a compatibility/recovery path.
- Changed keepalive and Tool Data request time fields to reuse a connection-time snapshot, matching newer MASSO Link capture analysis.
- Updated protocol documentation and regression-test targets to reflect the corrected interpretation.

## v1.8.19 RC — historical

- Fixed queue target-folder behavior.
- Changing the target MASSO folder after files are already in the queue now updates Pending and Failed queue items.
- Sending and Done queue items are left unchanged so the queue still shows where already-sent files actually went.
- Cleaned up repository documentation around install, testing, reporting, and protocol notes.

## v1.8.18 RC

- Restored larger-file uploads that were failing at the apparent 255/256 ACK boundary.
- Before this fix, a roughly 929 KB G-code file failed consistently at that point.
- A modulo-256 ACK workaround was added and outside testing then reported successful uploads of more than 10 files ranging from about 2 KB to 1200 KB.
- Later v1.8.20 analysis showed MASSO was not actually rolling an 8-bit counter; the app had been reading the wrong ACK byte offset/order. v1.8.20 replaces the workaround with the corrected 16-bit little-endian decoder.

## v1.8.17 RC

- Added named MASSO alarm/fault display for confirmed status codes.
- Known alarms include X/Y/Z/A/B Motor Alarm, Spindle Alarm, Air Pressure Low Alarm, Lubricant Low Alarm, and Torch Breakaway.
- Uploads remain blocked for any non-normal alarm/fault code.
- Unknown future codes display as `Fault / Alarm 0x??` and are still treated as unsafe.

## v1.8.16 RC

- Improved Tools Data workflow.
- **Get Tools Data** downloads MASSO tool slots 1-118.
- The app automatically generates and opens a MASSO-style text file after tool data is downloaded.
- Tool export is read-only and currently includes tool number plus tool name.
- **Generate Text File** remains as a manual fallback.

## v1.8.8 through v1.8.15 RC

- Added and refined the scrollable HMI-style main window.
- Improved Tools Data export and open-after-save behavior.
- Continued cleanup of queue/status UI behavior.

## v1.8.6 RC

- Corrected status bytes 13-16 to elapsed run time in seconds.
- Removed earlier line-number/feed-hold assumptions based on that field.
- Updated the UI label from line-number style wording to elapsed-time wording.

## v1.8.3 through v1.8.5 RC

- Added optional auto-clear of the queue after successful queue upload.
- Improved queue and status panel layout.
- Removed noisy raw/last-packet display from the normal UI.

## v1.8.1 through v1.8.2 RC

- Added QR-code generation for selected files and queue items.
- Added custom logo support.

## v1.8.0 RC

- Added batch/queue upload workflow.
- Settings and profiles stored beside the program for portable ZIP-style use.
- Improved status display and queue controls.

## v1.7.x development line

- Added folder-aware uploads.
- Added stronger filename/folder validation.
- Fixed repeat-upload behavior by recreating the upload socket for each file.
- Added compatibility handling for short final file transfers.
- Confirmed boundary behavior around very small files and files around one full upload block.

## Earlier development

- Basic UDP connection/status handling.
- Initial G-code upload sequence.
- Initial profile/config support.
- Protocol work based on Andrew's `masso-link-protocol-client` project plus additional packet captures and controller testing.
