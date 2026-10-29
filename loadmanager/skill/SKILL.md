---
name: load-manager
description: Use when working with large video or audio files - ffmpeg encodes, proxies, cuts, exports, long renders - on Windows or macOS, especially when the files live in Google Drive, Dropbox, OneDrive or iCloud Drive, when a render runs long, the computer sleeps, runs hot or loud, or disk space is tight. Covers the local work folder, the `lm` helper, hardware encoding (VideoToolbox, NVENC, AMF, QuickSync) and keeping the computer awake.
---

# Big-video workflow (Load Manager)

Helper: `{{LM}}` (written `lm` below; call it by that full path). A PreToolUse hook also blocks media tools that are pointed straight at a cloud folder, so following this skill avoids the block. Works the same on Windows and macOS.

## 1. Never process inside a cloud-synced folder

Google Drive, Dropbox, OneDrive and iCloud Drive keep files as on-demand placeholders. A tool that opens one either stalls or pulls the whole file down first, and writing a render into the folder starts an upload while the render is still running.

- Source lives in a cloud folder: run `lm fetch "<file or folder>"` first. It prints a plan, checks free disk space, and copies into `~/VideoWork/<project>/src` (with `out/` and `proxy/` next to it). Work only on that copy.
- Write renders to `~/VideoWork/<project>/out` (drafts to `proxy/`).
- Copy results back with `lm publish <file> "<cloud folder>"` only when the user asks for it. It never deletes anything and refuses to replace an existing different file unless `--force`; do not use `--force` unless the user explicitly says to replace that file.
- Never delete or overwrite the user's originals, in the cloud or locally. Listing a cloud folder (`ls`, `dir`) is cheap and fine; reading file contents (ffprobe, hashing, `cat`) triggers a download.
- If the hook blocks a command, follow its message. Use `LM_ALLOW_DRIVE=1` in front of a command only when the user has said that working in place is fine (a small file, say).

## 2. Check before heavy work

- Start a session with big files by running `lm doctor`. It is read-only and reports free disk space, the power plan or thermal state, the load from the cloud client, which hardware encoders this ffmpeg has, and runs a 1-second self-test of each.
- Free space must cover the source copy plus the output. If `lm fetch` refuses, give the user the numbers and the options (external drive, fewer files at a time). Do not delete files to make room.
- `lm fetch` retries a failed copy up to three times and records where each file came from. `lm status` shows every project's `src`, `proxy` and `out` sizes and the free space.
- To free space: `lm clean <project> --src` lists the local copies that are safe to drop, meaning the original is still in the cloud with the same size and the copy was not edited. Run it without `--yes` first, show the user the list, and add `--yes` only after they agree. `--proxy` removes proxies, which can be rebuilt. `lm` never touches `out/`, and neither should you without being asked.

## 3. Encoding

`lm encode IN OUT --preset proxy|fast|final` (add `--dry-run` to see the exact ffmpeg command first):

| preset | for | how |
|---|---|---|
| `proxy` | editing proxies, drafts | 720p H.264 on the hardware encoder, 6M |
| `fast` | quick deliverable, client preview | H.264 on the hardware encoder, 20M (use `--bitrate 40M` for 4K) |
| `final` | the file that gets delivered when quality per megabyte matters | libx264 CRF 17, preset slow (software) |

- Hardware encoder: VideoToolbox on a Mac; NVENC, AMF or QuickSync on Windows, whichever the machine really has (each is self-tested once). Much faster and cooler, but it needs a bitrate (no CRF) and gives less quality per bit than x264/x265. Use it for drafts and quick exports; use software for the final master unless the user prefers speed. `--sw` forces software. Without a working hardware encoder `lm` falls back to x264 on its own. `LM_HW=none|videotoolbox|nvenc|amf|qsv` overrides the choice.
- `--codec hevc` gives smaller files: with `fast` it uses the hardware HEVC encoder (or libx265 CRF 22 in software), with `final` libx265 CRF 20, which is slow. The proxy preset is always H.264 so any editor can read it.
- Many clips: `lm proxies <project>` writes a 720p proxy of every clip in `src/` into `proxy/`, keeps the computer awake for the whole batch, sends one notification at the end, and skips proxies that already exist, so after an interruption you simply run it again. One bad clip does not stop the batch; an unfinished proxy is removed (unless `--force` was used) rather than left looking complete.
- On a Mac, if hardware decoding misbehaves on some camera format (green frames, a hang), retry with `--no-hwdecode`.
- Every preset writes 8-bit SDR 4:2:0. If `lm` warns that the input looks 10-bit/HDR/log, stop and ask the user before flattening it.
- A trim that needs no re-encode: `-c copy` (cuts land on keyframes).
- `--low` runs the job at lower priority, so the computer stays usable while it renders in the background. Use it when the user is working at the same time.

## 4. Long jobs

- The Bash tool times out (about 2 minutes by default, 10 at most). Start renders in the background and check on them; do not block on them.
- `lm encode` keeps the computer awake while it runs and sends a notification at the end. Outside a terminal it writes progress to `~/VideoWork/.logs/encode-*.progress`; read the last block (`out_time=` against the clip's duration, and `speed=`).
- Any other long command: `lm run --name "what it is" -- <command>`. It adds the same stay-awake guard and notification, and logs to `~/VideoWork/.logs/runs.log`.
- A laptop only stays awake on mains power, and closing the lid still sleeps it unless an external display is connected. Tell the user to plug in and leave the lid open for long renders.
- If the computer is hot, loud or slow, run `lm doctor`, then `lm spikes` (recent busy periods and which programs caused them; needs the Load Manager app to have been running). Do not change power plans or settings yourself: the app's Power page does that, and only when the user asks.

## 5. Wrapping up

Report the output path, size and duration. At the end of a session show `lm status` and offer `lm clean`; delete only when the user asks.

## Troubleshooting

- "Operation not permitted" on a cloud folder (macOS): grant the app that runs Claude access under System Settings > Privacy & Security > Files and Folders or Full Disk Access.
- ffmpeg sits at 0 frames on a cloud path: the file is still downloading. Use `lm fetch` and watch the cloud client's icon.
- "Unknown encoder": the ffmpeg build lacks it. `lm doctor` shows what the current build has; a full build (macOS: `brew install ffmpeg`, Windows: `winget install ffmpeg`) has them.
- Final Cut, DaVinci Resolve and Premiere are not driven by this kit. If the user works in them, the same rule applies to their cache and render folders: keep them on the local disk.
