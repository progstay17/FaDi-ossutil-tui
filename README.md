# OSS Control Center (`fadi-oss-tui`)

A terminal UI for **Alibaba Cloud OSS**, built on top of [ossutil 2.x](https://www.alibabacloud.com/help/en/oss/developer-reference/ossutil-overview).
It is made for large uploads (tens or hundreds of GB) that take hours: you can pause and resume, the queue survives a crash, and every upload is verified.

> Status: early (v0.2.0), Windows only. Not affiliated with Alibaba Cloud.

[Bahasa Indonesia](README.id.md)

## What it does today

- **Dark Operations Console.** A focused dashboard with a navigation rail, live queue metrics, upload list, selected-job details, and transfer summary. The visual system uses a charcoal surface with lime accents while staying entirely inside the terminal.
- **Login once, stay logged in.** Enter AccessKey, region and a bucket; the app tests the connection (read-only) before saving. Log out any time with `L`.
- **Upload large single files** with pause, resume (also after the app or PC stops), live speed and ETA, and a check after upload.
- **Browse the bucket** (folders and files, paging, details) and pick the upload destination from the browser.
- **Catches typos in the destination.** If the destination folder does not exist, the pre-flight dialog says so and suggests similar existing folders (for example `team-alpha-black` when you typed `team-alpah-black`).

Not yet: folder or batch upload, delete / rename / move, several profiles, other operating systems.

## Requirements

- Windows 10 or 11 (credentials are protected with Windows DPAPI)
- Python 3.11 or newer (developed and tested with 3.14)
- ossutil 2.x (the installer can download 2.4.0 and verify its SHA-256 for you)
- [Windows Terminal](https://aka.ms/terminal) is recommended for the best display

## Install

Double-click `install.cmd`, or from a terminal in this folder:

```powershell
.\install.cmd -InstallOssutil -AddToPath
```

| Option | Effect |
|---|---|
| `-InstallOssutil` | Download ossutil 2.4.0 into the app folder and verify its SHA-256 |
| `-AddToPath` | Add the launcher folder to your user PATH, so `fadi-oss-tui` works in any terminal |
| `-NoShortcut` | Do not create the Start Menu shortcut |
| `-InstallDir <path>` | Install somewhere else (default `%LOCALAPPDATA%\FaDi-OSS-TUI`) |
| `-Python <python.exe>` | Use a specific Python |

The installer works for your user only (no administrator rights), uses a private virtual environment, and does not change anything system-wide. The `.cmd` wrappers start PowerShell with `-ExecutionPolicy Bypass` for that one script; read `install.ps1` first if you want to see exactly what it does.

## Run

- Start Menu: **OSS Control Center**, or
- `run.cmd` in this folder, or
- `fadi-oss-tui` (if you used `-AddToPath`, in a new terminal)

On the first start you get the login screen. After that it goes straight to the Upload Manager.

## Keys

**Upload Manager:** `A` add upload, `B` browse bucket, `S` start, `P` pause, `R` resume, `V` verify again, `X` remove from queue, `L` log out, `Q` quit (a running upload is paused first).

**Browser:** `Enter` open folder or show file details, `Backspace` up, `M` load more, `I` details (a folder's total size is counted from the bucket and can take a few seconds), `R` refresh, `S` select this folder (when opened from the Add dialog), `Esc` close.

## How your credentials are handled

- The AccessKey secret is encrypted with **Windows DPAPI** (bound to your Windows user) and stored in `profile.json`. It is never written in plain text.
- ossutil receives the keys only through **environment variables of its own process**, not on the command line and not in a config file. ossutil is given an empty config file, so an old `~/.ossutilconfig` cannot interfere.
- Limits: other programs running as the same Windows user can decrypt the secret, and logging out deletes the file but cannot guarantee physical erasure on an SSD. Use a RAM user with the minimum permissions.

## Where your data lives

`%LOCALAPPDATA%\FaDi-OSS-TUI\` holds `profile.json` (login), `queue.json` (upload queue), `logs\`, `checkpoints\`, plus the program itself (`venv\`, `bin\`, optionally `ossutil\`).

## Good to know

- One upload runs at a time.
- Pause stops the ossutil process; resume starts it again with the same checkpoint folder. Files above 100 MiB continue from their checkpoint; smaller files are sent again from the start of that file.
- Verification after upload checks that the object size matches and that a CRC64 is present. ossutil itself checks CRC64 while transferring; the local file's CRC64 is not recomputed and compared.
- Uploads never pass `-f`, so ossutil decides what happens when the object already exists (this case has not been tested).
- Some operations print an error about unfinished uploads when your RAM user lacks that permission; the app treats this as a warning.

## Uninstall

```powershell
.\uninstall.cmd                      # removes the program, keeps your login and queue
.\uninstall.cmd -RemoveData          # also deletes login, queue, logs and checkpoints (asks first)
```

The uninstaller only removes files the installer created and refuses to touch a folder that is not an installation.

## Development

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

The tests use fake ossutil processes and a fake bucket; they never contact OSS. Layout:

| Path | Purpose |
|---|---|
| `ossutil_tui/app.py` | Textual screens (login, upload manager, browser, dialogs) |
| `ossutil_tui/controller.py` | Jobs, upload / pause / resume / verify, browsing commands |
| `ossutil_tui/runner.py` | Runs ossutil, parses progress, pause |
| `ossutil_tui/profile.py`, `connection.py`, `context.py` | Login, DPAPI, connection test, app session |
| `ossutil_tui/browser.py`, `views.py`, `format.py` | Parsing of `ls -d` / `du`, display text |
| `tests/` | Unit and UI tests |
| `install.ps1`, `uninstall.ps1`, `*.cmd` | Installer, uninstaller, launchers |

## License

Not chosen yet. Until a license is added, all rights are reserved by the repository owner.