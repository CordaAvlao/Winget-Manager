# Winget Manager

A clean, native Windows GUI for **winget** — search packages, install them,
download installers, and keep your apps up to date without ever touching a
terminal.

Winget Manager wraps the `winget` CLI behind a lightweight Tkinter interface
with a Windows 11 look and feel (light & dark themes), real-time progress,
human-readable error messages, and bulk operations on multiple packages at
once.

> Requires Windows 10/11 with `winget` installed (App Installer from the
> Microsoft Store).

---

## Features

- **Updates tab** — lists every available update with current → available
  versions, filter, multi-select and bulk **update** or **download**.
- **Catalog tab** — search the winget repository, inspect package details
  (publisher, homepage, license…), then install, download or pin.
- **Real-time progress** — dedicated progress bars for downloads (with
  byte-level parsing like `21.0 MB / 67.1 MB`) and indeterminate mode for
  installs/updates.
- **Smart error decoding** — winget/MSIX error codes are translated into
  plain-language causes and concrete fixes (admin rights, package in use,
  disk space, network…).
- **Admin elevation** — when an update needs elevated privileges, a
  "Restart as administrator" button appears automatically.
- **Download management** — detects already-downloaded installers and asks
  whether to **replace**, **skip**, or **cancel** the whole batch.
- **Light & dark themes** — Windows 11 styling via [sv_ttk](https://github.com/rdbende/Sun-Valley-ttk-theme), persisted between sessions.
- **Live log console** — timestamped activity feed where file paths are
  clickable to open them directly in Explorer.
- **Pin / ignore packages** — keep a package at its current version so it
  stops showing up in updates.
- **No console window** — ships with a `.pyw` launcher and a fully
  self-contained `.exe` build.

---

## Installation

### Option 1 — Portable executable (recommended)

1. Go to the [Releases](../../releases) page.
2. Download `Winget Manager.exe`.
3. Double-click — that's it. Python is bundled, nothing to install.

### Option 2 — Run from source

Requires **Python 3.8+** and `winget`.

```bash
git clone https://github.com/CordaAvlao/Winget-Manager.git
cd Winget-Manager
pip install sv_ttk
python "Winget Manager.pyw"
```

> `sv_ttk` is optional — if it's missing, Winget Manager falls back to the
> native Tkinter theme automatically.

### Option 3 — Build your own executable

Requires Python, `pyinstaller` and `sv_ttk`:

```bash
pip install pyinstaller sv_ttk
build_exe.bat
```

The self-contained `Winget Manager.exe` is generated in `dist/`.

---

## Usage

1. Launch **Winget Manager**. The **Updates** tab populates automatically with
   available updates.
2. Use the filter box to narrow the list, then tick the packages you care
   about (or *Select all*).
3. Choose an action:
   - **Update selection** — upgrade every checked package.
   - **Download selection** — fetch the installers to your download folder.
   - **Pin selection** — freeze packages at their current version.
4. Switch to the **Catalog** tab to discover and install new software by name.
5. Watch the live log at the bottom for real-time feedback and clickable
   file paths.

A `config.json` file is created next to the executable on first run to
remember your download folder and theme preference.

---

## Why this project exists

`winget` is powerful, but it's a command-line tool. That's fine for
developers, but awkward for everyday use — checking for updates, downloading
an offline installer, or understanding why an install failed shouldn't
require memorizing flags and hex error codes.

Winget Manager brings winget to a friendly desktop UI while staying
dependency-light (a single Python file, a stdlib GUI, one optional theme
package) and fully portable.

---

## Tech Stack

- **Python 3** — Tkinter (standard library) for the UI.
- **sv_ttk** — Sun Valley theme for a Windows 11 look.
- **PyInstaller** — produces the standalone `.exe`.
- **winget** — the underlying package manager (Windows 10/11).

---

## Project Structure

```
Winget-Manager/
├── winget_manager.py      # Core logic + GUI (single file)
├── Winget Manager.pyw     # Console-free launcher
├── build_exe.bat          # One-click PyInstaller build script
├── icon.ico               # Application icon
├── config.json            # Generated at runtime (theme + download folder)
├── README.md
└── LICENSE
```

---

## Roadmap

- Export / import the list of installed packages.
- Scheduled background update checks with notifications.
- Per-package changelog viewer.
- Localization (EN / ES / DE).

---

## Contributing

Contributions are welcome. To keep things smooth:

1. Fork the repo and create a feature branch.
2. Keep the single-file architecture and the separation between winget
   business logic and UI code.
3. Test against real `winget` operations before submitting.
4. Open a pull request describing the change and why it matters.

Bug reports and feature ideas are just as valuable — please use the
[Issues](../../issues) tab.

---

## Support the project

If you enjoy this project and want to support future development:

https://www.paypal.com/ncp/payment/NPGMPUL9N9TFQ

Your support helps improve the project and maintain future updates.

---

## License

Released under the [MIT License](LICENSE).

---

## Author

**CordaAvlao**
