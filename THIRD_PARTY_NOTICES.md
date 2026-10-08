# Third-party notices

These notices cover the v2 desktop application and its packaging dependencies. Send-to-MASSO Manager's own code is licensed under the [MIT License](LICENSE). Third-party components retain their own copyrights and license terms.

## MASSO protocol client and research

Send-to-MASSO Manager includes or derives portions of code and protocol research from Andrew's [`masso-link-protocol-client`](https://github.com/andrewpc/masso-link-protocol-client) project. The upstream README identifies its license as MIT.

Andrew's work provided the starting point for the UDP connection, status monitoring, upload sequence, and tool-data exploration. The current implementation resides in `masso_core.py` and includes this project's subsequent protocol corrections and compatibility work.

Retain the upstream attribution and any copyright and license notices supplied with the upstream code. This repository's `LICENSE` describes Send-to-MASSO Manager's own license; it does not replace upstream notices.

## Desktop runtime dependencies

| Component | Use | License information |
| --- | --- | --- |
| [Qt for Python / PySide6-Essentials](https://doc.qt.io/qtforpython-6/) | Qt Core, GUI, and Widgets for the v2 interface | Installed package metadata declares `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. Qt libraries and included third-party components have their own applicable terms. |
| [Shiboken6](https://doc.qt.io/qtforpython-6/shiboken6/) | Runtime support for the Python Qt bindings; installed through PySide6-Essentials | Installed package metadata declares `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. |
| [qrcode](https://github.com/lincolnloop/python-qrcode) | Creates MASSO QR codes | BSD 3-Clause; its license file also preserves attribution to the MIT-licensed code from which it was derived. Copyright 2011 Lincoln Loop, with original QR implementation attribution to Kazuhiko Arase. |
| [Pillow](https://python-pillow.github.io/) | Renders and saves QR-code PNG images | MIT-CMU for Pillow/PIL; the wheel's full license file includes notices for bundled libraries. Copyrights include Secret Labs AB, Fredrik Lundh and contributors, and Jeffrey 'Alex' Clark and contributors. |
| [Python](https://www.python.org/) | Interpreter and standard library, included in standalone builds | Python Software Foundation License Version 2 and additional notices for incorporated software; see [Python's license documentation](https://docs.python.org/3/license.html). |

The package declarations above were checked against the local v2 build environment. Dependency versions are selected by `requirements.txt`; a different build may contain different versions or bundled libraries.

For Qt and its incorporated components, consult the official [Qt for Python license notices](https://doc.qt.io/qtforpython-6/licenses.html) and [Qt licensing documentation](https://doc.qt.io/qt-6/licensing.html). This document summarizes attribution; the full component licenses govern their use and distribution.

## Packaging tools

[PyInstaller](https://pyinstaller.org/) builds the Windows executable and macOS application. It is a build dependency listed in `requirements-build.txt`; its bootloader is included in packaged applications.

PyInstaller is licensed under GPL version 2 or later with an exception for distributing applications built with it. See the [PyInstaller license and exception](https://pyinstaller.org/en/stable/license.html) for the complete terms.

## Notices in packaged distributions

`build_desktop.py` includes this document, the project `LICENSE`, and metadata for PySide6-Essentials, Shiboken6, qrcode, and Pillow. The qrcode and Pillow metadata directories include their supplied license files.

Package metadata alone is not a complete set of dependency license texts. Preserve the full applicable copyright, license, and third-party notices for the Python runtime, Qt libraries and plugins, bindings, image libraries, and packaging runtime included in each release. Qt's installed packages in the checked environment do not supply standalone license files in their distribution metadata, so copying that metadata does not supply those texts.

## Independence and trademarks

Send-to-MASSO Manager is independent software and is not affiliated with or endorsed by MASSO, The Qt Company, or the other upstream projects named here. Product and project names remain the property of their respective owners.
