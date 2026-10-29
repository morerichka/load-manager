"""The video helpers (lm fetch / encode / proxies / run / publish / status / clean / doctor), against a stand-in ffmpeg."""
import argparse
import os
import sys
import time
from pathlib import Path

import pytest

from loadmanager import video

FAKE = str(Path(__file__).with_name("fake_ffmpeg.py"))
ENC = {"proxy": 6, "fast": 20}


@pytest.fixture
def lm(tmp_path, monkeypatch, capsys):
    for name in list(os.environ):
        if name.startswith(("VK_", "LM_", "FAKE_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LM_WORK", str(tmp_path / "VideoWork"))
    monkeypatch.setenv("LM_MIN_FREE_GB", "0")
    monkeypatch.setenv("LM_RETRY_DELAY", "0")
    monkeypatch.setenv("LM_NOTIFY", "0")
    monkeypatch.setenv("LM_FFMPEG", f'"{sys.executable}" "{FAKE}"')
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "ffmpeg.log"))
    monkeypatch.setattr(video, "system", lambda: "Linux")
    video._encoders.cache_clear()
    video._selftest.cache_clear()

    def run(*args):
        capsys.readouterr()
        code = video.main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err
    run.work = tmp_path / "VideoWork"
    run.tmp = tmp_path
    return run


def make(path, size=2000, fill=b"x"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fill * size)
    return path


@pytest.fixture
def cloud(tmp_path):
    root = tmp_path / "Google Drive" / "My Drive" / "Wedding 2026"
    make(root / "a.mov", 3000)
    make(root / "sub" / "b.mp4", 5000)
    make(root / ".DS_Store", 10)
    make(root / "c.mov.lmpart.tmp", 10)
    return root


# ---------- small things ----------

def test_sizes_read_naturally():
    assert [video.human(n) for n in (0, 1023, 1024, 1536, 5 * 1024 ** 3)] == ["0 B", "1023 B", "1.0 KB", "1.5 KB", "5.0 GB"]


@pytest.mark.parametrize("name,ok", [("wedding", True), ("W-2026_a.b", True), ("", False), (".hidden", False), ("a/b", False),
                                     ("a b", False), ("..", False), ("-x", False)])
def test_project_names_are_safe_folder_names(name, ok):
    if ok:
        assert video.valid_project(name) == name
    else:
        with pytest.raises(video.Fail):
            video.valid_project(name)


def test_the_project_name_comes_from_the_folder(tmp_path):
    folder = make(tmp_path / "Wedding 2026 (copy)" / "a.mov").parent
    assert video.project_from(folder) == "Wedding-2026-copy"
    assert video.project_from(folder / "a.mov") == "Wedding-2026-copy"
    (tmp_path / "!!!").mkdir()
    assert video.project_from(tmp_path / "!!!") == "project"


def test_old_vk_names_still_work_as_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("LM_WORK", raising=False)
    monkeypatch.setenv("VK_WORK", str(tmp_path / "old"))
    assert video.work_dir() == tmp_path / "old"
    monkeypatch.setenv("LM_WORK", str(tmp_path / "new"))
    assert video.work_dir() == tmp_path / "new"                   # LM_ wins


# ---------- the cloud guard inside the helpers ----------

def test_the_work_folder_may_not_be_inside_the_cloud(lm, tmp_path, monkeypatch, cloud):
    monkeypatch.setenv("LM_WORK", str(tmp_path / "Dropbox" / "VideoWork"))
    code, _, err = lm("fetch", str(cloud))
    assert code == 4 and "refusing to work in place" in err
    monkeypatch.setenv("LM_ALLOW_DRIVE", "1")
    assert lm("setup")[0] == 0


def test_encode_and_run_refuse_cloud_paths(lm, cloud, monkeypatch):
    code, _, err = lm("encode", str(cloud / "a.mov"), str(lm.tmp / "o.mp4"))
    assert code == 4 and "lm fetch" in err
    make(lm.tmp / "x.mov")
    assert lm("encode", str(lm.tmp / "x.mov"), str(cloud / "o.mp4"))[0] == 4
    assert lm("run", "--", sys.executable, "-c", "pass", str(cloud / "a.mov"))[0] == 4
    monkeypatch.setattr(os, "getcwd", lambda: str(cloud))
    assert lm("run", "--", "ffmpeg", "-i", "x")[0] == 4


# ---------- fetch ----------

def test_fetch_copies_a_folder_with_a_plan_a_manifest_and_the_original_times(lm, cloud):
    os.utime(cloud / "a.mov", (1_600_000_000, 1_600_000_000))
    code, out, _ = lm("fetch", str(cloud))
    base = lm.work / "Wedding-2026" / "src" / "Wedding 2026"
    assert code == 0 and "Plan: 2 file(s)" in out and "Copied 2, skipped 0, failed 0" in out
    assert (base / "a.mov").read_bytes() == b"x" * 3000 and (base / "sub" / "b.mp4").exists()
    assert int((base / "a.mov").stat().st_mtime) == 1_600_000_000
    assert not (base / ".DS_Store").exists() and not list(base.rglob("*.lmpart.tmp"))          # junk and part files are not copied
    assert (lm.work / "Wedding-2026" / "proxy").is_dir() and (lm.work / "Wedding-2026" / "out").is_dir()
    rows = [l.split("\t") for l in (lm.work / "Wedding-2026" / ".lm-manifest.tsv").read_text().splitlines()]
    assert sorted(r[1] for r in rows) == ["src/Wedding 2026/a.mov", "src/Wedding 2026/sub/b.mp4"] and rows[0][0] in ("3000", "5000")


def test_fetching_again_skips_what_is_there_and_never_duplicates_the_record(lm, cloud):
    lm("fetch", str(cloud))
    code, out, _ = lm("fetch", str(cloud))
    assert code == 0 and "Copied 0, skipped 2" in out and out.count("already there, skipped") == 2
    assert len(video.manifest_latest("Wedding-2026")) == 2


def test_a_dry_run_copies_nothing(lm, cloud):
    code, out, _ = lm("fetch", "--dry-run", str(cloud))
    assert code == 0 and "dry run" in out and not lm.work.exists()


def test_a_single_file_and_an_explicit_project(lm, cloud):
    assert lm("fetch", "--project", "mine", str(cloud / "a.mov"))[0] == 0
    assert (lm.work / "mine" / "src" / "a.mov").exists()
    code, _, err = lm("fetch", "--project", "bad name", str(cloud / "a.mov"))
    assert code == 1 and "invalid project name" in err


def test_a_missing_source_is_an_error_and_nothing_is_created(lm, cloud):
    code, _, err = lm("fetch", str(cloud / "nope.mov"))
    assert code == 1 and "not found" in err and not lm.work.exists()


def test_fetch_refuses_when_the_disk_would_not_hold_it(lm, cloud, monkeypatch):
    monkeypatch.setenv("LM_MIN_FREE_GB", "99999999")
    code, _, err = lm("fetch", str(cloud))
    assert code == 1 and "not enough free space" in err and not lm.work.exists()


def test_a_failed_copy_is_retried_then_reported(lm, cloud, monkeypatch):
    real = video.copy_file
    attempts = []

    def flaky(src, dst, refuse_diff=False):
        attempts.append(Path(src).name)
        return "failed" if Path(src).name == "a.mov" else real(src, dst, refuse_diff)
    monkeypatch.setattr(video, "copy_file", flaky)
    code, out, _ = lm("fetch", str(cloud))
    assert code == 1 and attempts.count("a.mov") == 3 and "Copied 1, skipped 0, failed 1" in out
    assert len(video.manifest_latest("Wedding-2026")) == 1                       # only what really arrived is on record


def test_a_copy_that_succeeds_on_the_second_try_counts(lm, cloud, monkeypatch):
    real, calls = video.copy_file, []
    monkeypatch.setattr(video, "copy_file", lambda s, d, r=False: (calls.append(1), "failed" if len(calls) == 1 else real(s, d, r))[1])
    assert video.copy_retry(cloud / "a.mov", lm.tmp / "dst" / "a.mov") == "copied" and len(calls) == 2


def test_a_broken_copy_leaves_no_part_file(tmp_path, monkeypatch):
    src = make(tmp_path / "s.bin", 5000)
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk full")))
    assert video.copy_file(src, tmp_path / "d" / "s.bin") == "failed"
    assert not list((tmp_path / "d").glob("*"))


def test_an_interrupted_copy_cleans_up(tmp_path, monkeypatch):
    src = make(tmp_path / "s.bin", 5000)
    real_open = open

    class Boom:
        def __init__(self, f):
            self.f = f
        def __enter__(self):
            self.f.__enter__()
            return self
        def __exit__(self, *a):
            return self.f.__exit__(*a)
        def read(self, n):
            raise KeyboardInterrupt
        def write(self, b):
            return self.f.write(b)
    monkeypatch.setattr(video, "open", lambda p, m="r", *a, **k: Boom(real_open(p, m, *a, **k)) if "rb" in m else real_open(p, m, *a, **k), raising=False)
    with pytest.raises(KeyboardInterrupt):
        video.copy_file(src, tmp_path / "d" / "s.bin")
    assert not list((tmp_path / "d").glob("*"))


def test_a_different_file_is_not_replaced_when_asked_not_to(tmp_path):
    src, dst = make(tmp_path / "s.bin", 500), make(tmp_path / "d.bin", 7)
    assert video.copy_file(src, dst, refuse_diff=True) == "refused" and dst.read_bytes() == b"x" * 7
    assert video.copy_file(src, dst) == "copied" and dst.stat().st_size == 500


def test_verify_compares_contents(lm, cloud):
    code, out, _ = lm("fetch", "--verify", str(cloud))
    assert code == 0 and out.count("verified") == 2


# ---------- publish ----------

def test_publish_copies_back_without_deleting_or_replacing(lm, cloud):
    result = make(lm.tmp / "out" / "final.mp4", 800)
    dest = lm.tmp / "Google Drive" / "My Drive" / "Deliveries"
    dest.mkdir(parents=True)
    code, out, _ = lm("publish", str(result), str(dest))
    assert code == 0 and (dest / "final.mp4").stat().st_size == 800 and result.exists()
    assert "uploads in the background" in out
    make(result, 900)
    code, _, err = lm("publish", str(result), str(dest))
    assert code == 1 and "different file already exists" in err and (dest / "final.mp4").stat().st_size == 800
    assert lm("publish", "--force", str(result), str(dest))[0] == 0 and (dest / "final.mp4").stat().st_size == 900
    code, out, _ = lm("publish", str(result), str(dest))
    assert code == 0 and "already there" in out


def test_publish_a_folder_and_the_error_cases(lm):
    folder = make(lm.tmp / "out" / "set" / "a.mp4", 100).parent
    make(folder / "deep" / "b.mp4", 100)
    dest = lm.tmp / "dest"
    dest.mkdir()
    assert lm("publish", str(folder), str(dest))[0] == 0
    assert (dest / "set" / "a.mp4").exists() and (dest / "set" / "deep" / "b.mp4").exists()
    assert lm("publish", str(folder), str(lm.tmp / "nowhere"))[0] == 1
    assert lm("publish", str(lm.tmp / "ghost.mp4"), str(dest))[0] == 1
    assert lm("publish", str(dest))[0] == 1                                       # a destination only


# ---------- status and clean ----------

def test_status_before_and_after(lm, cloud):
    code, out, _ = lm("status")
    assert code == 0 and "does not exist yet" in out
    lm("fetch", str(cloud))
    make(lm.work / "Wedding-2026" / "out" / "x.mp4", 400)
    code, out, _ = lm("status")
    assert "Wedding-2026" in out and "2 file(s)" in out and "originals on record: 2" in out and "1 file(s)" in out


def fetched(lm, cloud):
    lm("fetch", str(cloud))
    return lm.work / "Wedding-2026"


def test_clean_lists_first_and_only_removes_what_is_provably_safe(lm, cloud):
    project = fetched(lm, cloud)
    code, out, _ = lm("clean", "Wedding-2026", "--src")
    assert code == 0 and "Would remove 2 file(s)" in out and "Nothing was removed" in out and (project / "src" / "Wedding 2026" / "a.mov").exists()
    code, out, _ = lm("clean", "Wedding-2026", "--src", "--yes")
    assert "Removed 2 file(s)" in out and not list((project / "src").rglob("*.mov")) and not (project / "src" / "Wedding 2026").exists()
    assert (project / "out").is_dir()
    assert video.manifest_latest("Wedding-2026") == []


@pytest.mark.parametrize("what,message", [("gone", "no longer at"), ("size", "different size"), ("edited", "changed after the fetch")])
def test_clean_keeps_a_copy_when_the_original_cannot_vouch_for_it(lm, cloud, what, message):
    project = fetched(lm, cloud)
    if what == "gone":
        (cloud / "a.mov").unlink()
    elif what == "size":
        make(cloud / "a.mov", 3001)
    else:
        make(project / "src" / "Wedding 2026" / "a.mov", 1234)
    code, out, _ = lm("clean", "Wedding-2026", "--src", "--yes")
    assert message in out and (project / "src" / "Wedding 2026" / "a.mov").exists()
    assert not (project / "src" / "Wedding 2026" / "sub" / "b.mp4").exists()      # the other one was fine


def test_clean_leaves_files_it_did_not_fetch_and_never_touches_out(lm, cloud):
    project = fetched(lm, cloud)
    mine = make(project / "src" / "mine.mov", 50)
    out_file = make(project / "out" / "keep.mp4", 50)
    code, out, _ = lm("clean", "Wedding-2026", "--src", "--yes")
    assert "1 file(s) in src/ with no record" in out and mine.exists() and out_file.exists()


def test_clean_proxies(lm, cloud):
    project = fetched(lm, cloud)
    make(project / "proxy" / "p1.mp4", 100)
    code, out, _ = lm("clean", "Wedding-2026", "--proxy", "--yes")
    assert "Removed 1 file(s)" in out and not list((project / "proxy").rglob("*.mp4")) and (project / "src" / "Wedding 2026" / "a.mov").exists()


@pytest.mark.parametrize("args,message", [(["nope", "--src"], "no such project"), (["Wedding-2026"], "say what to clean"),
                                          (["../x", "--src"], "invalid project name")])
def test_clean_checks_its_arguments(lm, cloud, args, message):
    fetched(lm, cloud)
    code, _, err = lm("clean", *args)
    assert code == 1 and message in err


def test_clean_never_follows_a_symlink(lm, cloud):
    project = fetched(lm, cloud)
    target = make(lm.tmp / "precious.bin", 10)
    link = project / "src" / "Wedding 2026" / "a.mov"
    link.unlink()
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not allowed here")
    lm("clean", "Wedding-2026", "--src", "--yes")
    assert target.exists()


# ---------- encode: planning (pure) ----------

def plan(preset="fast", codec="h264", bitrate=None, hw=None, encoders=("libx264", "libx265"), hwdecode=True, force=False,
         out="/o/x.mp4", extra=(), progress="/p.progress", interactive=False):
    return video.plan_encode(preset, codec, bitrate, hw, set(encoders), hwdecode, force, "/in/a.mov", out, extra, progress,
                             interactive, ("ffmpeg",))


def flags(cmd):
    return " ".join(cmd)


def test_the_mac_presets_use_the_same_flags_as_the_shell_kit_did():
    cmd, enc, hardware, _ = plan("fast", hw="videotoolbox", encoders=("h264_videotoolbox",))
    assert hardware and enc == "h264_videotoolbox"
    assert flags(cmd) == ("ffmpeg -hide_banner -nostdin -n -hwaccel videotoolbox -i /in/a.mov -c:v h264_videotoolbox -b:v 20M "
                          "-profile:v high -pix_fmt yuv420p -c:a aac -b:a 192k -sn -dn -movflags +faststart -loglevel warning "
                          "-nostats -progress /p.progress /o/x.mp4")
    cmd, *_ = plan("fast", "hevc", hw="videotoolbox", encoders=("h264_videotoolbox", "hevc_videotoolbox"))
    assert "-c:v hevc_videotoolbox -b:v 12M -tag:v hvc1" in flags(cmd)
    cmd, *_ = plan("proxy", hw="videotoolbox", bitrate="8M")
    assert "-vf scale=-2:'min(720,ih)' -c:v h264_videotoolbox -b:v 8M" in flags(cmd) and "-b:a 128k" in flags(cmd)


def test_each_windows_encoder_gets_its_own_flags():
    for backend, expect in (("nvenc", "-c:v h264_nvenc -preset p5 -rc vbr -b:v 20M -profile:v high"),
                            ("amf", "-c:v h264_amf -usage transcoding -quality balanced -b:v 20M"),
                            ("qsv", "-c:v h264_qsv -b:v 20M -profile:v high")):
        cmd, enc, hardware, _ = plan("fast", hw=backend, encoders=(f"h264_{backend}",))
        assert expect in flags(cmd) and hardware and enc == f"h264_{backend}"
        assert "-hwaccel" not in flags(cmd)                                      # hardware decoding is a Mac-only default
        cmd, *_ = plan("fast", "hevc", hw=backend, encoders=(f"h264_{backend}", f"hevc_{backend}"))
        assert f"-c:v hevc_{backend}" in flags(cmd) and "-tag:v hvc1" in flags(cmd)


def test_software_presets_and_the_final_preset_never_use_hardware():
    assert "-c:v libx264 -preset fast -crf 19" in flags(plan("fast")[0])
    assert "-c:v libx264 -preset veryfast -crf 23" in flags(plan("proxy")[0])
    cmd, enc, hardware, _ = plan("final", hw="videotoolbox", encoders=("h264_videotoolbox", "libx264"))
    assert "-c:v libx264 -preset slow -crf 17" in flags(cmd) and not hardware and "-hwaccel" not in flags(cmd) and "-b:a 256k" in flags(cmd)
    assert "-c:v libx265 -preset slow -crf 20 -tag:v hvc1" in flags(plan("final", "hevc")[0])
    assert "-c:v libx265 -preset medium -crf 22 -tag:v hvc1" in flags(plan("fast", "hevc")[0])


def test_hevc_needs_an_encoder_that_exists():
    with pytest.raises(video.Fail, match="libx265"):
        plan("fast", "hevc", encoders=("libx264",))
    with pytest.raises(video.Fail, match="libx265"):
        plan("final", "hevc", encoders=("libx264",))
    cmd, *_ = plan("fast", "hevc", hw="amf", encoders=("h264_amf", "libx265"))             # no hevc_amf: falls back to software
    assert "libx265" in flags(cmd)


def test_warnings_for_options_that_have_no_effect():
    assert any("proxy" in w for w in plan("proxy", "hevc")[3])
    assert any("final" in w for w in plan("final", bitrate="9M")[3])
    assert plan("fast")[3] == []


def test_output_flags_depend_on_the_target():
    assert "-movflags +faststart" in flags(plan(out="/o/x.mp4")[0]) and "-movflags" in flags(plan(out="/o/x.MOV")[0])
    assert "-movflags" not in flags(plan(out="/o/x.mkv")[0])
    assert "-n" in plan(force=False)[0] and "-y" in plan(force=True)[0]
    assert "-stats" in plan(interactive=True)[0] and "-progress" not in plan(interactive=True)[0]
    cmd = plan(extra=("-t", "5"))[0]
    assert cmd[-3:] == ["-t", "5", "/o/x.mp4"]
    assert "-hwaccel" not in flags(plan("fast", hw="videotoolbox", hwdecode=False, encoders=("h264_videotoolbox",))[0])


# ---------- encode: hardware choice ----------

@pytest.mark.parametrize("system,encoders,failing,expected", [
    ("Darwin", "h264_videotoolbox,hevc_videotoolbox,libx264", "", "videotoolbox"),
    ("Darwin", "h264_videotoolbox", "h264_videotoolbox", "videotoolbox"),        # the Mac's encoder is trusted when listed
    ("Windows", "h264_nvenc,h264_amf,libx264", "", "nvenc"),
    ("Windows", "h264_nvenc,h264_amf,libx264", "h264_nvenc", "amf"),              # no NVIDIA GPU: the listed encoder fails its self-test
    ("Windows", "h264_nvenc,h264_amf,h264_qsv", "h264_nvenc,h264_amf", "qsv"),
    ("Windows", "h264_nvenc,h264_amf", "h264_nvenc,h264_amf", None),
    ("Windows", "libx264", "", None),
    ("Darwin", "h264_nvenc,libx264", "", None),                                   # an encoder for another system is ignored
])
def test_the_hardware_encoder_is_the_first_one_that_really_works(lm, monkeypatch, system, encoders, failing, expected):
    monkeypatch.setattr(video, "system", lambda: system)
    monkeypatch.setenv("FAKE_ENCODERS", encoders)
    monkeypatch.setenv("FAKE_SELFTEST_FAIL", failing)
    assert video.hardware_backend() == expected


def test_the_hardware_choice_can_be_forced_or_switched_off(lm, monkeypatch):
    monkeypatch.setattr(video, "system", lambda: "Windows")
    monkeypatch.setenv("FAKE_ENCODERS", "h264_nvenc,h264_amf,libx264")
    monkeypatch.setenv("LM_HW", "amf")
    assert video.hardware_backend() == "amf"
    video._selftest.cache_clear()
    monkeypatch.setenv("LM_HW", "none")
    assert video.hardware_backend() is None
    monkeypatch.setenv("LM_HW", "qsv")                                            # not in this build
    assert video.hardware_backend() is None


# ---------- encode: the command itself ----------

def encode(lm, *args):
    return lm("encode", *args)


def test_encode_writes_the_output_and_reports(lm):
    src = make(lm.tmp / "in.mov", 4000)
    code, out, _ = encode(lm, str(src), str(lm.tmp / "o" / "out.mp4"), "--preset", "final")
    assert code == 0 and (lm.tmp / "o" / "out.mp4").exists() and "Encoder: libx264 (software)" in out and "Done:" in out
    logged = Path(os.environ["FAKE_LOG"]).read_text()
    assert "-c:v libx264 -preset slow -crf 17" in logged and "-progress" in logged


def test_encode_uses_the_hardware_encoder_and_sw_overrides_it(lm, monkeypatch):
    monkeypatch.setattr(video, "system", lambda: "Windows")
    monkeypatch.setenv("FAKE_ENCODERS", "h264_amf,libx264")
    src = make(lm.tmp / "in.mov", 4000)
    assert "h264_amf (hardware)" in encode(lm, str(src), str(lm.tmp / "a.mp4"))[1]
    assert "libx264 (software)" in encode(lm, str(src), str(lm.tmp / "b.mp4"), "--sw")[1]


def test_a_dry_run_shows_the_command_and_creates_nothing(lm):
    src = make(lm.tmp / "in.mov", 4000)
    code, out, _ = encode(lm, str(src), str(lm.tmp / "deep" / "o.mp4"), "--dry-run")
    assert code == 0 and "Command:" in out and "dry run" in out and not (lm.tmp / "deep").exists()


def test_extra_ffmpeg_arguments_go_before_the_output(lm):
    src = make(lm.tmp / "in.mov", 4000)
    assert encode(lm, str(src), str(lm.tmp / "o.mp4"), "--preset", "final", "--", "-t", "5")[0] == 0
    assert "-t 5 " in Path(os.environ["FAKE_LOG"]).read_text()


@pytest.mark.parametrize("extra,message", [(["--preset", "slowest"], "unknown preset"), (["--codec", "av1"], "unknown codec")])
def test_encode_checks_its_options(lm, extra, message):
    src = make(lm.tmp / "in.mov")
    code, _, err = encode(lm, str(src), str(lm.tmp / "o.mp4"), *extra)
    assert code == 1 and message in err


def test_encode_refuses_missing_input_existing_output_and_missing_space(lm, monkeypatch):
    src = make(lm.tmp / "in.mov", 4000)
    assert "input not found" in encode(lm, str(lm.tmp / "ghost.mov"), str(lm.tmp / "o.mp4"))[2]
    make(lm.tmp / "o.mp4", 7)
    code, _, err = encode(lm, str(src), str(lm.tmp / "o.mp4"))
    assert code == 1 and "output exists" in err and (lm.tmp / "o.mp4").stat().st_size == 7
    assert encode(lm, str(src), str(lm.tmp / "o.mp4"), "--force")[0] == 0 and (lm.tmp / "o.mp4").stat().st_size > 7
    monkeypatch.setenv("LM_MIN_FREE_GB", "99999999")
    assert "not enough free space" in encode(lm, str(src), str(lm.tmp / "new.mp4"))[2]


def test_a_missing_ffmpeg_says_how_to_get_it(lm, monkeypatch):
    monkeypatch.setenv("LM_FFMPEG", "definitely-not-installed-ffmpeg")
    src = make(lm.tmp / "in.mov")
    code, _, err = encode(lm, str(src), str(lm.tmp / "o.mp4"))
    assert code == 1 and "ffmpeg not found" in err and "brew install ffmpeg" in err and "winget" in err


def test_a_failed_encode_removes_the_incomplete_output_unless_forced(lm, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_ON", "bad")
    src = make(lm.tmp / "bad.mov", 4000)
    code, _, err = encode(lm, str(src), str(lm.tmp / "o.mp4"))
    assert code == 1 and not (lm.tmp / "o.mp4").exists() and "removed the incomplete output" in err
    code, _, err = encode(lm, str(src), str(lm.tmp / "o.mp4"), "--force")
    assert code == 1 and (lm.tmp / "o.mp4").exists() and "may be incomplete" in err


def test_ten_bit_sources_get_a_warning(lm, monkeypatch):
    monkeypatch.setenv("FAKE_PIXFMT", "yuv420p10le")
    src = make(lm.tmp / "in.mov", 4000)
    assert "looks 10-bit/HDR" in encode(lm, str(src), str(lm.tmp / "o.mp4"))[2]


def test_a_low_priority_job_is_started_with_the_lower_class(lm, monkeypatch):
    seen, real = [], video.subprocess.Popen

    def spy(cmd, **kw):                                           # only the encode itself, not the probes before it
        if "-nostdin" in cmd:
            seen.append(kw)
        return real(cmd, **kw)
    monkeypatch.setattr(video.subprocess, "Popen", spy)
    src = make(lm.tmp / "in.mov", 4000)
    encode(lm, str(src), str(lm.tmp / "o.mp4"), "--low")
    encode(lm, str(src), str(lm.tmp / "p.mp4"))
    assert (seen[0].get("creationflags") == 0x4000 if os.name == "nt" else callable(seen[0].get("preexec_fn"))) and seen[1] == {}


# ---------- proxies ----------

def test_proxies_make_one_per_clip_and_a_rerun_continues(lm, cloud, monkeypatch):
    fetched(lm, cloud)
    code, out, _ = lm("proxies", "Wedding-2026")
    proxies = sorted(p.relative_to(lm.work / "Wedding-2026" / "proxy").as_posix() for p in (lm.work / "Wedding-2026" / "proxy").rglob("*.mp4"))
    assert code == 0 and proxies == ["Wedding 2026/a.mp4", "Wedding 2026/sub/b.mp4"] and "2 made, 0 already existed, 0 failed" in out
    code, out, _ = lm("proxies", "Wedding-2026")
    assert code == 0 and "0 made, 2 already existed" in out
    assert lm("proxies")[0] == 0                                                   # one project: no name needed


def test_one_bad_clip_does_not_stop_the_batch_and_leaves_no_half_proxy(lm, cloud, monkeypatch):
    fetched(lm, cloud)
    monkeypatch.setenv("FAKE_FAIL_ON", "b.mp4")
    code, out, _ = lm("proxies", "Wedding-2026")
    proxy = lm.work / "Wedding-2026" / "proxy" / "Wedding 2026"
    assert code == 1 and "1 made, 0 already existed, 1 failed" in out
    assert (proxy / "a.mp4").exists() and not (proxy / "sub" / "b.mp4").exists()


def test_an_empty_proxy_is_made_again(lm, cloud):
    project = fetched(lm, cloud)
    empty = make(project / "proxy" / "Wedding 2026" / "a.mp4", 0)
    assert lm("proxies", "Wedding-2026")[0] == 0 and empty.stat().st_size > 0


def test_two_clips_with_one_proxy_name_are_not_mixed_up(lm):
    base = lm.work / "p" / "src"
    make(base / "a.mov", 100)
    make(base / "a.mp4", 100)
    code, out, err = lm("proxies", "p")
    assert code == 1 and "map to the same proxy name" in err and "1 made" in out


def test_proxies_check_the_project_and_the_preset(lm, cloud):
    assert "say which project" in lm("proxies")[2]
    fetched(lm, cloud)
    make(lm.work / "other" / "src" / "x.mov")
    assert "say which project (found: Wedding-2026, other)" in lm("proxies")[2]
    assert "does not exist" in lm("proxies", "ghost")[2]
    (lm.work / "empty" / "src").mkdir(parents=True)
    assert "no video files" in lm("proxies", "empty")[2]
    assert "preset must be proxy or fast" in lm("proxies", "Wedding-2026", "--preset", "final")[2]


def test_the_hardware_choice_is_made_once_for_the_whole_batch(lm, cloud, monkeypatch):
    fetched(lm, cloud)
    calls = []
    real = video.hardware_backend
    monkeypatch.setattr(video, "hardware_backend", lambda: (calls.append(1), real())[1])
    lm("proxies", "Wedding-2026")
    assert len(calls) == 1


# ---------- run, awake, notify ----------

def test_run_returns_the_exit_code_and_writes_a_log_line(lm):
    assert lm("run", "--name", "hello", "--", sys.executable, "-c", "print('hi')")[0] == 0
    assert lm("run", "--", sys.executable, "-c", "import sys; sys.exit(3)")[0] == 3
    lines = (lm.work / ".logs" / "runs.log").read_text().splitlines()
    assert lines[0].split("\t")[1:3] == ["hello", "exit=0"] and lines[1].split("\t")[2] == "exit=3"


def test_run_needs_a_command_and_reports_a_missing_one(lm):
    assert "nothing to run" in lm("run")[2] and "nothing to run" in lm("run", "--")[2]
    code, _, err = lm("run", "--", "definitely-not-a-program-xyz")
    assert code == 127 and "command not found" in err


def test_a_notification_is_sent_for_run_but_not_inside_a_batch(lm, monkeypatch):
    sent = []
    monkeypatch.setattr(video, "notify", lambda *a: sent.append(a))
    lm("run", "--name", "job", "--", sys.executable, "-c", "pass")
    lm("run", "--name", "quiet", "--no-notify", "--", sys.executable, "-c", "pass")
    lm("run", "--name", "bad", "--", sys.executable, "-c", "import sys; sys.exit(1)")
    assert [m[1] for m in sent][0].startswith("job finished") and "FAILED (exit 1)" in sent[1][1] and len(sent) == 2


def test_on_a_mac_a_long_job_runs_under_caffeinate_that_ends_with_it(lm, monkeypatch):
    started = []

    class Helper:
        def terminate(self):
            started.append("terminated")
    monkeypatch.setattr(video, "system", lambda: "Darwin")
    monkeypatch.setattr(video.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(video.subprocess, "Popen", lambda args, **kw: (started.append(args), Helper())[1])
    with video.awake():
        started.append("working")
    assert started[0] == ["caffeinate", "-ims", "-w", str(os.getpid())] and started[1:] == ["working", "terminated"]


def test_on_windows_a_long_job_holds_a_stay_awake_request(lm, monkeypatch):
    events = []

    class Request:
        def __init__(self, reason):
            events.append(("new", reason))
        def hold(self):
            events.append("hold")
        def close(self):
            events.append("close")
    monkeypatch.setattr(video, "system", lambda: "Windows")
    monkeypatch.setattr("loadmanager.winapi.WakeRequest", Request)
    with video.awake():
        events.append("working")
    assert events[1:] == ["hold", "working", "close"]


def test_a_refused_stay_awake_request_is_a_warning_not_a_failure(lm, monkeypatch, capsys):
    class Request:
        def __init__(self, reason):
            pass
        def hold(self):
            raise OSError("no")
    monkeypatch.setattr(video, "system", lambda: "Windows")
    monkeypatch.setattr("loadmanager.winapi.WakeRequest", Request)
    with video.awake():
        pass
    assert "may sleep" in capsys.readouterr().err


def test_notifications_cannot_inject_commands(monkeypatch):
    monkeypatch.delenv("LM_NOTIFY", raising=False)
    monkeypatch.delenv("VK_NOTIFY", raising=False)
    calls = []
    monkeypatch.setattr(video, "system", lambda: "Windows")
    monkeypatch.setattr(video.subprocess, "Popen", lambda args, **kw: calls.append(args))
    video.notify("T'itle", "x'; Start-Process calc; '\"$(evil)`")
    script = calls[0][-1]
    # the quote characters that could end the PowerShell string are gone, so the text stays inside it
    assert "ShowBalloonTip(6000,'Title','x; Start-Process calc; (evil)','Info')" in script
    ran = []
    monkeypatch.setattr(video, "system", lambda: "Darwin")
    monkeypatch.setattr(video.shutil, "which", lambda n: "/usr/bin/osascript")
    monkeypatch.setattr(video.subprocess, "run", lambda args, **kw: ran.append(args))
    video.notify('Ti"tle', r'a "quoted" \ message')
    assert ran[0] == ["osascript", "-e", 'display notification "a quoted  message" with title "Title"']


def test_notifications_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("LM_NOTIFY", "0")
    monkeypatch.setattr(video.subprocess, "Popen", lambda *a, **k: pytest.fail("a notification was sent"))
    video.notify("a", "b")


# ---------- doctor, setup, the command line ----------

def test_doctor_reports_without_changing_anything(lm, monkeypatch):
    monkeypatch.setenv("FAKE_ENCODERS", "h264_nvenc,h264_qsv,libx264")
    monkeypatch.setenv("FAKE_SELFTEST_FAIL", "h264_qsv")
    monkeypatch.setattr(video, "system", lambda: "Windows")
    code, out, _ = lm("doctor")
    assert code == 0 and "[ok]" in out and "ffmpeg version fake" in out
    assert "self-test: h264_nvenc encoded" in out and "self-test: h264_qsv FAILED" in out and "libx264 available" in out
    assert not lm.work.exists()


def test_doctor_on_a_mac_does_not_crash_when_the_mac_tools_are_missing(lm, monkeypatch):
    monkeypatch.setattr(video, "system", lambda: "Darwin")
    code, out, _ = lm("doctor")
    assert code == 0 and "macOS" in out


def test_doctor_says_when_ffmpeg_is_missing(lm, monkeypatch):
    monkeypatch.setenv("LM_FFMPEG", "definitely-not-installed-ffmpeg")
    code, out, _ = lm("doctor")
    assert code == 0 and "ffmpeg not found" in out


def test_setup_creates_the_work_folder(lm):
    code, out, _ = lm("setup")
    assert code == 0 and lm.work.is_dir() and "Work folder ready" in out


def test_the_command_line_has_help_and_clear_errors(lm, capsys):
    assert video.main([]) == 2
    assert "COMMAND" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        video.main(["encode"])                                                   # arguments missing: argparse's own error


def test_ffmpeg_can_be_given_as_a_path_with_spaces_or_a_whole_command(monkeypatch):
    monkeypatch.setenv("LM_FFMPEG", '"C:/Program Files/ffmpeg/bin/ffmpeg.exe"')
    assert video.ffmpeg_cmd() == ["C:/Program Files/ffmpeg/bin/ffmpeg.exe"]
    monkeypatch.setenv("LM_FFMPEG", "python fake.py")
    assert video.ffmpeg_cmd() == ["python", "fake.py"]
    monkeypatch.delenv("LM_FFMPEG")
    assert video.ffmpeg_cmd() == ["ffmpeg"]


def test_a_clip_that_cannot_even_be_started_does_not_stop_the_batch_either(lm, cloud, monkeypatch):
    fetched(lm, cloud)
    real = video.encode_one

    def picky(args, **kw):
        if args.input.endswith("a.mov"):
            raise video.Fail("encode: this clip is unreadable")
        return real(args, **kw)
    monkeypatch.setattr(video, "encode_one", picky)
    code, out, err = lm("proxies", "Wedding-2026")
    assert code == 1 and "unreadable" in err and "1 made, 0 already existed, 1 failed" in out
    assert (lm.work / "Wedding-2026" / "proxy" / "Wedding 2026" / "sub" / "b.mp4").exists()
