# Load Manager

**A process priority and memory manager for Windows and macOS.**

Load Manager makes room for focused work by monitoring local resource use and applying explicit, reversible changes to the apps that actually hold the CPU and RAM during work: Chrome, Claude, Telegram and Google Drive sync (Windows), plus light launchers such as Spotify and Discord. The interface is in English.

**Status: 0.8.0 developer preview.** This is a real Python desktop application with a native system webview, not a website that claims to control your computer. The browser preview uses clearly labeled sample data. Native Windows/macOS binaries still require platform testing and signing before a public release.

The dashboard uses a charcoal palette, lime accents, a live load chart, and a responsive layout. Open the bundled preview to explore the interface.

## What works

- Local CPU, RAM, system-disk free space, aggregate disk I/O throughput, battery, and process monitoring every two seconds. One tick takes about 0.1 s of CPU (version 0.3 took about 1.8 s: asking Windows about every process one by one is slow); a monitor that burns a core would defeat the purpose.
- Dashboard with two minutes of load history, process search/filtering, profiles, and an action log.
- **Windows, heavy apps (chosen as a whole group, so tabs and helpers that appear later are covered):**
  - Google Chrome, Telegram, Google Drive sync: lower priority (below normal in Focus, idle in Render) and EcoQoS (the same state as Task Manager's Efficiency mode).
  - Claude: memory trim only. Its priority is never lowered, because render tools it starts would inherit the lower class.
  - When RAM use is 70% or higher, the selected apps' working sets are trimmed to the standby list. Nothing is deleted or closed; pages load back on demand. The log shows how many MB were freed.
- **Windows, Render profile:** while the session runs, `ffmpeg`, DaVinci Resolve and Premiere are raised above normal priority, including ones started later (a tool started by a lowered parent would otherwise inherit the lower class). Their original priority returns when the session ends.
- **Windows, light apps** (Spotify, Steam, Epic, Discord, Slack): priority only, picked one by one.
- **macOS / Linux:** only the light apps, and only by explicit suspend/resume. Suspension does **not** free RAM, so Chrome, Claude and the other heavy apps stay protected there.
- **Auto-start on render (Render page, off by default):** when `ffmpeg`, Resolve or Premiere keeps using at least half a CPU core, the Render profile is applied to the chosen heavy apps; about 20 seconds after the render stops, the session ends. A session you started yourself is never touched, and if you end one during a render it is not started again until that render finishes. Every render is logged with its duration (accurate to a few seconds), whether or not auto-start is on.
- **Timed real command (Render page):** type the command you actually run (for example an `ffmpeg` line). *Run A/B* runs it once without a session and once with one, and shows both times and the time saved. The order can be swapped, because the run that goes second may profit from files already in the cache. The command is started directly (no shell) and its run time alone is measured. *Cancel* stops only the command this feature started.
- **Spike log (Spikes page, on by default):** a busy period starts when the whole-machine CPU stays above 40% for 6 seconds and ends after 10 seconds below 25% (both levels can be changed). For every period it keeps the length, average and peak CPU, the power plan, whether you were on battery, and the programs behind it, grouped by program with their share of the CPU time on all cores. System services (antivirus, search indexer, svchost) are included because the list comes from one system call, not from per-process queries; work that was started by a Claude session (a child of `claude.exe`) is labelled *via Claude*, and Load Manager's own work *via Load Manager*. Only program names are stored, never command lines. The last 50 periods are kept in `history.json`.
- **Quiet mode (Power page, Windows):** makes the laptop quieter while you work and puts everything back when you leave it, close the app, or after a crash (what was active is written to the recovery journal before anything is changed, and the guardian process restores it). Two methods:
  - *Switch the power plan:* to a quieter one (Silent, else Balanced, or the one you pick).
  - *Limit the processor:* keeps your plan, with its sleep and display settings, and changes only **turbo boost** (turned off) and/or the **maximum processor state** (lowered) inside it. Measured on the ROG Zephyrus G14 (Ryzen AI 9 HX 370): turning boost off took the busiest core from about 250% to 101% of the base clock, and one busy thread ran about 60% slower; the maximum state, the maximum frequency, the energy preference and the other boost modes had **no effect at all** on that processor, so some processors simply ignore them.
  - While quiet mode is on and work is running (a session, a timed run, a render tool, or a busy period), the laptop is **kept from going to sleep** with a Windows power request, and may sleep again a minute after the work ends; this can be switched off.
  - Renders (a Render session, auto-start, a timed run) use the original plan or settings, and quiet mode returns when they finish; a timed A/B runs both phases on the same settings so the comparison stays fair. If you or the laptop's own software change the plan or one of the settings, quiet mode steps aside and never changes it back. Optionally it starts together with the app. Ending a session by hand leaves quiet mode on. Plans differ in more than speed: on the tested laptop the Silent plan sleeps and turns the display off after 5 minutes instead of 30, so check what the plan you pick changes.
- **Real measurement:** the *CPU benchmark* panel on the overview page runs a fixed multi-threaded CPU job at normal priority, once before and once during a session, and shows the difference. `python -m loadmanager --benchmark` does the same from a terminal. It measures CPU contention only (not disk, network or GPU).
- **Auto select:** chooses Focus or Render at session start based on current render-app CPU use. **Render mode** on the overview page selects Render plus every running heavy app and opens the plan.
- Safety checks: exact name catalogue, same-user checks, executable identity checks, PID creation-time checks, and protection for the app's own process tree.
- Write-ahead recovery journal (priority, EcoQoS and inherited-helper records), a separate crash guardian, startup recovery, and failure reporting. These reduce the chance of leaving a process changed; they cannot guarantee recovery after every OS/security failure.
- **Video page (Windows and Mac):** every `lm` command as a form in the window, with the system file dialogs, a progress bar, a live copy of what the command prints, Cancel, a queue of jobs, sleep / hibernate / shut down when everything is done, and a list of recent jobs (see "The same commands as buttons" below).
- No account, remote API, analytics, cache deletion, process termination, administrator escalation, or modifications to system services.

## One tool for Windows and Mac: the Claude kit and `lm`

Besides the window, Load Manager now includes what used to be a separate "Mac Video Kit", rewritten in Python so the same code and the same install work on both systems. It is for people who edit **big video files** with Claude and keep them in **Google Drive, Dropbox, OneDrive or iCloud Drive**.

- **Drive guard** (a Claude Code hook): stops Claude from running ffmpeg / HandBrake / `lm encode` directly on a cloud-synced folder. Those files can be on-demand placeholders, so the tool stalls or drags the whole file down first, and a render written into the folder starts uploading before it is finished. Claude gets a message that says what to do instead. It is conservative: anything it cannot read or understand is allowed.
- **Local work folder:** `lm fetch` copies what you need into `~/VideoWork/<project>/src` (free space checked first, progress, a size check, three retries, a journal of where each file came from); `lm publish` copies results back only when asked and never replaces a different file without `--force`.
- **ffmpeg with the hardware encoder the machine really has:** `lm encode` has the presets `proxy`, `fast` and `final`. It picks VideoToolbox on a Mac, and NVENC, AMF or QuickSync on Windows, whichever passes a one-second self-test, and falls back to software x264/x265. Hardware is for drafts and quick exports; `final` is software for quality per megabyte. `LM_HW=none|videotoolbox|nvenc|amf|qsv` overrides the choice (on a laptop with two GPUs, `LM_HW=amf` uses the integrated one).
- **Stays awake and says when it is done:** long jobs hold a stay-awake request (`caffeinate` on a Mac, a Windows power request on Windows), log to `~/VideoWork/.logs/runs.log` and notify at the end. `--low` runs a job at lower priority so the computer stays usable.
- **A skill** (`load-manager`) that teaches Claude the whole routine, so you can just say what you want.

Install (needs Python 3.11 or newer; for encoding also ffmpeg: macOS `brew install ffmpeg`, Windows `winget install ffmpeg`):

| | |
|---|---|
| Windows | double-click **INSTALL-Claude-Kit.cmd** |
| macOS | double-click **INSTALL-Claude-Kit.command** |

Then restart Claude and ask it to run `lm doctor`. The installer creates a private Python environment in this folder (keep the folder where it is), writes the skill and a launcher into `~/.claude/`, and adds one hook entry to `~/.claude/settings.json` after saving a backup. `lm uninstall-claude` removes exactly that; `~/VideoWork` is never touched.

| command | what it does |
|---|---|
| `lm doctor` | free space, power plan or thermal state, the cloud client's load, which hardware encoders work (self-tested), keep-awake support |
| `lm fetch "<file or folder>"` | copy out of the cloud into `~/VideoWork/<project>/src` (`--dry-run`, `--verify`, `--project NAME`) |
| `lm encode IN OUT --preset proxy\|fast\|final` | ffmpeg with sensible settings (`--codec hevc`, `--dry-run`, `--sw`, `--low`, `--no-hwdecode` on a Mac, `-- extra ffmpeg args`) |
| `lm proxies <project>` | a 720p proxy of every clip; a rerun continues where it stopped; one bad clip does not stop the rest |
| `lm run -- <command>` | keep the computer awake while any long command runs, notify at the end |
| `lm publish <file> "<cloud folder>"` | copy results back |
| `lm status` | every project's sizes and the free disk space |
| `lm clean <project> --src` | free disk space: lists the local copies whose original is still in the cloud with the same size (`--yes` deletes them); `--proxy` lists proxies; `out/` is never touched |
| `lm spikes` | recent busy periods and which programs caused them (recorded by the app) |
| `lm app` | the desktop window |

### The same commands as buttons: the Video page

Every command above is also a form in the window (menu: **Video**), so nothing has to be typed:

| tab | what you do | command behind it |
|---|---|---|
| 1 · Copy from cloud | choose files or a folder with the system file dialog (or paste a path), optionally name the project, press "Check the plan" or "Copy" | `lm fetch` |
| 2 · Proxies | pick a project and the quality | `lm proxies` |
| 3 · Encode | choose the input; the output name is suggested inside the project; pick preset and codec; "Show the command" or "Encode" | `lm encode` |
| 4 · Copy back | add a project's finished renders (or any files), choose the cloud folder | `lm publish` |
| Free space | "Preview", then a separate red button deletes exactly what was previewed (and only with the same choices) | `lm clean` |
| Run a command | keeps the computer awake while any command runs | `lm run` |
| Health check | the report as OK / Check / Info rows | `lm doctor` |
| Claude kit | whether the guard, the skill and the launcher are in place; Install / Remove | `lm install-claude`, `lm uninstall-claude` |

Above the tabs is the work folder: free disk space, whether ffmpeg was found, every project's sizes with shortcuts (Open, Proxies, Free space), "Create folder" (`lm setup`) and settings for these tools (work folder, which video encoder, how much disk space to keep free, where ffmpeg is). Below them is the running job: its title, the exact command line (copyable), progress with speed and time left for encodes and copies, what the command prints, Cancel, "Show the result in its folder", and a list of recent jobs.

How jobs behave: one at a time; each is a separate `lm` process, started without a shell, with the settings of this page and nothing inherited from the shell that started the app. Cancel works like Ctrl+C in a terminal (a copy removes its half-written file, a running ffmpeg is stopped, an unfinished proxy is removed, a batch does not go on to the next clip); a job that has not reacted after 12 seconds is stopped for good. While a job runs the menu item shows a dot, and the laptop is kept awake. Closing the window during a job holds the first attempt back; closing again within 15 seconds cancels the job and quits. The settings on this page apply to jobs started from the window; the command line keeps using its own `LM_*` environment variables.

**The queue.** While a job runs, the buttons that start work (Copy, Make proxies, Encode, Copy to the cloud, Run) turn into **Add to queue**, and a Queue panel appears under the tabs. Items run one after another, each starting when the one before it has ended. An item may name a file that an earlier item will make (copy from the cloud, then encode the copy): its form is checked when you add it, its files when its turn comes, and an item that cannot start is listed as failed. Reorder with the arrows, remove with ×; **Pause queue** lets you collect a plan first and **Resume** starts it. If you cancel a job the queue pauses and keeps the rest; if a job fails it pauses too, unless **Keep going when a job fails** is ticked. Previews, deleting, the health check and the Claude kit are never queued (they answer at once, or delete). The waiting items are saved: after a restart they are back, paused, and never start by themselves.

**When everything is done:** choose *Do nothing*, *Put the computer to sleep*, *Hibernate* or *Shut down* (a Mac offers sleep and shut down only). When the last job has ended, or the queue has stopped because a job failed, a 60-second countdown starts with a banner in the window and a notification; **Cancel** stops it, and starting new work stops it too. The choice is for this time only: it is forgotten once it has happened or been cancelled, is not kept when the app restarts, and never fires after a job you cancelled yourself. Shut down does not force applications closed, so a program with unsaved work can hold it back, and a refusal by the computer (hibernation switched off, say) is shown under the choice. Windows sleep is requested with the system's own sleep call; it was not tried on a real computer (see VALIDATION.md).

Good to know: all presets write 8-bit SDR (a 10-bit/HDR/log input prints a warning). Closing a laptop's lid can still put it to sleep. On a Mac, "Operation not permitted" on a cloud folder means the app that runs Claude needs access under System Settings > Privacy & Security. To let one command work in a cloud folder anyway, put `LM_ALLOW_DRIVE=1` in front of it; Claude is told to do that only if you say it is fine. Final Cut, DaVinci Resolve and Premiere are not driven by this kit.

**What has been verified:** everything above on Windows 11 with a real ffmpeg (NVENC and AMF encoders self-test and encode; software x264/x265; proxies; fetch / publish / clean; the hook run exactly as Claude Code runs it; install and uninstall against a temporary Claude folder), plus 390 automated checks. **Not verified on a real Mac:** the VideoToolbox flags, `caffeinate`, `osascript` notifications and the output formats of `pmset` and `sw_vers`. The Windows-only parts (priorities, EcoQoS, power plans) do not exist on a Mac, where the window still offers monitoring, the spike log and pausing light apps.

## Quick start

For the ZIP edition, extract the whole folder and double-click **START-Windows.cmd** or **START-Mac.command**. Python 3.11+ must already be installed. The launcher creates a private `.loadmanager-venv`, installs dependencies on the first run, and opens the app. Later launches reuse it. **CREATE-Desktop-Shortcut.cmd** adds a Windows desktop shortcut with the Load Manager icon. See **START-HERE.txt** for step-by-step instructions. No administrator privileges or execution-policy changes are requested.

The shortcut uses a console-free entry point; startup failures are recorded in `loadmanager-startup.log` and shown in a dialog when the platform supports it. This source package is not a standalone precompiled installer.

Requires Python 3.11+ (3.12 recommended). Use a regular user account, **not administrator/root**. Windows requires the Microsoft Edge WebView2 Runtime; macOS uses its system WebKit. Linux GUI development additionally requires a pywebview-supported GTK or Qt backend.

```bash
git clone <your-repository-url>
cd loadmanager
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Then:

```bash
python -m pip install -e ".[desktop]"
python -m loadmanager
```

If shell activation is restricted, invoke `.venv\Scripts\python.exe` or `.venv/bin/python` directly. No execution-policy change is needed.

**Usage:** on *Overview*, press *Run* in the benchmark panel, then *Render mode*, review the plan and press *Apply plan*, start your render, and run the benchmark again. Or do it by hand: *Profiles* → pick a profile → *Apps* → tick the heavy-app groups and light processes → *Review plan*. *End session* restores the original settings. On macOS a pause also stops music, messages or calls in the selected process.

The target list is a catalogue in `loadmanager/engine.py` (`APPS`). An app that is missing from it, or whose executable cannot be inspected, remains protected. To add an app, give it a name set and an action list (`priority`, `eco`, `trim`); heavy apps are Windows-only and are selected as a group. Do not add generic runtimes, editors or agents: lowering a parent process changes what its future children inherit.

## Interface preview

Open `loadmanager/ui/index.html` in a browser, or export a single file:

```bash
python -m loadmanager --export-demo loadmanager-preview.html
```

The preview labels all metrics and actions as demo data and never accesses processes. A responsive iPhone layout is a preview of the interface only; an iOS app and remote companion protocol are **not implemented**.

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
npm ci
npx playwright install chromium
npm test
```

Python tests cover protection rules, PID reuse, priority/EcoQoS rollback, group expansion with browser tabs coming and going, memory trim thresholds, inherited priorities, the render boost, the benchmark, partial failures, crash-journal recovery, and read-only monitoring. The POSIX crash test uses disposable subprocesses and is skipped in environments whose `/proc` namespace does not match child PIDs. Browser checks cover navigation, filtering, protected apps, demo apply/restore, small screens, and the absence of simulated data in the native shell.

For a browser-free functional check of the interface, run `npm run test:dom` (the main pages, and the Video page end to end in the demo and against answers shaped like the real backend's: `tests/video_contract.json` is checked against the real backend by `tests/test_video_bridge.py`). This verifies DOM behavior but does not replace visual or native-webview testing.

The Video page's backend has its own tests (`tests/test_video_jobs.py`, `tests/test_video_queue.py`, `tests/test_video_bridge.py`; the queue tests replace the power call, and a guard refuses to put the computer to sleep or shut it down from under pytest): form values to command lines, settings, and real jobs that run the real `lm` code against a stand-in ffmpeg, including progress, cancelling and closing the window. With a real ffmpeg installed, `tests/test_video_real_ffmpeg.py` also runs real encodes through the job runner (progress while it runs, a cancel that leaves no ffmpeg and no half file).

GitHub Actions checks run on Windows, macOS, and Linux. Build workflows are triggered manually or with a `v*` tag; they produce unsigned artifacts, not public releases. Workflow configuration is included, but results must be checked in the destination repository.

An opt-in test exercises the real Windows calls on disposable copies of `cmd.exe` (never your own apps). It saturates all CPU threads for a few minutes in total and prints the before/after benchmark, a real timed A/B, and checks that auto-start follows a real `ffmpeg.exe` look-alike:

```powershell
$env:LOADMANAGER_LIVE = "1"
python -m pytest tests/test_windows_live.py -s
```

## Build desktop bundles

Build separately on each target OS and architecture:

```bash
python -m pip install -e ".[desktop,dev]"
python scripts/build.py
```

Output: `dist/LoadManager/LoadManager.exe` on Windows, `dist/LoadManager.app` on macOS. Distribute the whole Windows folder, not just the `.exe`. These are development bundles. Code signing, notarization, an installer, auto-update, and native GUI smoke tests are release work, not completed features. Do not disable OS security protections to run an untrusted build.

## Architecture

```text
loadmanager/engine.py    Local metrics, app catalogue, policy, action journal, rollback, benchmark
loadmanager/winapi.py    Windows-only EcoQoS, working-set, process table and power request (ctypes)
loadmanager/cli.py       One entry point: `lm` and `python -m loadmanager`
loadmanager/jobs.py      The Video page's backend: form values to `lm` command lines, one job at a time with live output, progress and cancel
loadmanager/video.py     fetch / encode / proxies / run / publish / status / clean / doctor (stdlib only)
loadmanager/guard.py     The Claude Code hook that keeps media tools out of cloud folders (stdlib only)
loadmanager/cloud.py     Which paths are cloud-synced folders
loadmanager/install_claude.py  Installs the hook, the skill and the launcher into ~/.claude
loadmanager/skill/       The skill text Claude reads
loadmanager/automation.py  Auto-start on render, timed A/B of a real command
loadmanager/power.py     Quiet mode: power-plan switching with a journaled way back
loadmanager/recorder.py  Spike recorder and per-process attribution
loadmanager/app.py       Native webview bridge, lifecycle, crash guardian
loadmanager/ui/          Dependency-free HTML/CSS/JS interface and demo
tests/                Engine and browser checks
scripts/build.py      Native bundle preparation
.github/workflows/    Cross-platform checks and unsigned builds
```

The native shell embeds trusted local HTML and uses a small JS-to-Python bridge. It does not expose an HTTP control API. Process names are escaped before being rendered. A restrictive content policy blocks external assets and connections. Monitoring starts read-only; changing a profile alone does not change system state.

Original process identities and priorities are stored atomically in the per-user state directory, together with `settings.json` (auto-start choices, last timed command) and `history.json` (render log, timed runs):

- Windows: `%LOCALAPPDATA%/Load Manager`
- macOS: `~/Library/Application Support/LoadManager`
- Linux: `$XDG_STATE_HOME/loadmanager`, defaulting to `~/.local/state/loadmanager`

Runtime state, process lists, logs, credentials, and machine information must never be committed to Git.

## Current limits and next work

1. Time your real lesson render with *Run A/B* a few times, in both orders. Results vary from run to run (two runs of the synthetic test saved 21% and 51%), and a disk or GPU limit shows up as no gain. No speedup is promised.
2. Validate WebView behavior, crash recovery, and packaging on each target OS, then add signed releases.
3. Add integrations for n8n, mailings and old-cache cleanup, and a Mac equivalent that can restore priorities (`nice` cannot be lowered back without root, which is why Mac still uses pause).
4. Add authenticated device pairing before implementing an iOS companion.
5. Add localization and broader, reviewed application policies.

CPU priority and EcoQoS affect CPU contention; they do not accelerate a cloud model or remove GPU/disk bottlenecks. Trimming memory helps only when RAM is nearly full; otherwise it causes avoidable page faults, which is why it is applied from 70% use. While a session runs, the selected apps are deliberately slower (Chrome tabs, calls, video); end the session to get them back.

## References

- [psutil API and platform support](https://psutil.io/)
- [pywebview API](https://pywebview.flowrl.com/api/)
- [Windows scheduling priorities](https://learn.microsoft.com/en-us/windows/win32/procthread/scheduling-priorities)
- [Apple application sandbox](https://support.apple.com/guide/security/sec15bfe098e/web)

Copyright © 2026 morerichka. All rights reserved. No open-source license has been selected yet.
