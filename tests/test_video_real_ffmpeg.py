"""The same helpers against a REAL ffmpeg (skipped when none is installed). Short clips only: two seconds each.

Set LM_TEST_FFMPEG to a particular ffmpeg to test with it. Whatever hardware encoder the machine has is used for
the 'auto' tests, so on a laptop these also prove that the self-test picks one that really works."""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import psutil

from loadmanager import video
from test_video_jobs import Box


def find_ffmpeg():
    for candidate in (os.environ.get("LM_TEST_FFMPEG"), shutil.which("ffmpeg"),
                      str(Path.home() / "Videos" / "deutsch-dialoge" / "node_modules" / "ffmpeg-static" / "ffmpeg.exe")):
        if candidate and Path(candidate).exists() or (candidate and shutil.which(candidate)):
            return candidate
    return None


FFMPEG = find_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="no ffmpeg installed")


@pytest.fixture
def lm(tmp_path, monkeypatch, capsys):
    for name in list(os.environ):
        if name.startswith(("VK_", "LM_", "FAKE_")) and name != "LM_TEST_FFMPEG":
            monkeypatch.delenv(name)
    monkeypatch.setenv("LM_WORK", str(tmp_path / "VideoWork"))
    monkeypatch.setenv("LM_MIN_FREE_GB", "0")
    monkeypatch.setenv("LM_NOTIFY", "0")
    monkeypatch.setenv("LM_FFMPEG", f'"{FFMPEG}"')
    video._encoders.cache_clear()
    video._selftest.cache_clear()

    def run(*args):
        capsys.readouterr()
        code = video.main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err
    run.tmp = tmp_path
    run.work = tmp_path / "VideoWork"
    return run


def ffmpeg(*args):
    return subprocess.run([FFMPEG, "-hide_banner", *args], capture_output=True, text=True, errors="replace", timeout=120)


def make_clip(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    done = ffmpeg("-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=duration=2:size=1920x1080:rate=25", "-f", "lavfi", "-i",
                  "sine=frequency=440:duration=2", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                  "-shortest", str(path))
    assert done.returncode == 0, done.stderr
    return path


def streams(path):
    info = ffmpeg("-i", str(path)).stderr
    video_line = next(l for l in info.splitlines() if "Video:" in l)
    return {"codec": re.search(r"Video: (\w+)", video_line).group(1), "size": re.search(r"(\d{3,5})x(\d{3,5})", video_line).groups(),
            "pix": "yuv420p" in video_line, "audio": "Audio: aac" in info}


def decodes(path):
    return ffmpeg("-v", "error", "-i", str(path), "-f", "null", "-").returncode == 0


def test_the_software_proxy_is_a_720p_h264_that_plays(lm):
    clip = make_clip(lm.tmp / "in.mp4")
    code, out, _ = lm("encode", str(clip), str(lm.tmp / "p.mp4"), "--preset", "proxy", "--sw")
    info = streams(lm.tmp / "p.mp4")
    assert code == 0 and "libx264 (software)" in out and decodes(lm.tmp / "p.mp4")
    assert info == {"codec": "h264", "size": ("1280", "720"), "pix": True, "audio": True}


def test_the_final_preset_keeps_the_size(lm):
    clip = make_clip(lm.tmp / "in.mp4")
    assert lm("encode", str(clip), str(lm.tmp / "f.mp4"), "--preset", "final")[0] == 0
    assert streams(lm.tmp / "f.mp4")["size"] == ("1920", "1080") and decodes(lm.tmp / "f.mp4")


@pytest.mark.parametrize("preset,codec", [("proxy", "h264"), ("fast", "h264"), ("fast", "hevc")])
def test_whatever_hardware_this_machine_has_makes_a_file_that_plays(lm, preset, codec):
    clip = make_clip(lm.tmp / "in.mp4")
    code, out, _ = lm("encode", str(clip), str(lm.tmp / "o.mp4"), "--preset", preset, "--codec", codec)
    assert code == 0 and decodes(lm.tmp / "o.mp4")
    expect = "h264" if preset == "proxy" else codec
    assert streams(lm.tmp / "o.mp4")["codec"] == ("hevc" if expect == "hevc" else "h264")
    print(f"{preset}/{codec}: {re.search(r'Encoder: (.*)', out).group(1)}")


def test_a_batch_of_proxies_all_play_and_a_rerun_does_nothing(lm):
    for name in ("a.mp4", "b.mp4"):
        make_clip(lm.work / "proj" / "src" / name)
    code, out, _ = lm("proxies", "proj")
    proxies = sorted((lm.work / "proj" / "proxy").glob("*.mp4"))
    assert code == 0 and len(proxies) == 2 and all(decodes(p) for p in proxies) and "2 made" in out
    assert "0 made, 2 already existed" in lm("proxies", "proj")[1]


def test_a_run_of_the_real_thing_keeps_a_log(lm):
    code, _, _ = lm("run", "--name", "probe", "--", FFMPEG, "-version")
    assert code == 0 and "probe" in (lm.work / ".logs" / "runs.log").read_text()


def test_the_doctor_sees_the_real_ffmpeg_and_tests_what_it_finds(lm):
    code, out, _ = lm("doctor")
    assert code == 0 and "ffmpeg version" in out and "libx264" in out
    assert out.count("self-test:") == len(re.findall(r"^\[(?:ok|warn)\]\s+self-test:", out, re.M))


# ---------- the same through the window's job runner ----------

def make_long_clip(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    done = ffmpeg("-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=duration={seconds}:size=1280x720:rate=25", "-f", "lavfi",
                  "-i", f"sine=frequency=440:duration={seconds}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                  "-c:a", "aac", "-shortest", str(path))
    assert done.returncode == 0, done.stderr
    return path


@pytest.fixture
def real_box(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    box.jobs.extra_env["LM_FFMPEG"] = f'"{FFMPEG}"'
    return box


def test_a_real_encode_through_the_job_runner_shows_progress_and_ends_with_a_playable_file(real_box):
    clip = make_long_clip(real_box.work / "demo" / "src" / "long.mp4", 40)
    out = real_box.work / "demo" / "out" / "long-final.mp4"
    real_box.jobs.start("encode", {"input": str(clip), "output": str(out), "preset": "final", "sw": True})
    seen, speeds = [], []
    while real_box.jobs.running:
        view = real_box.jobs.state()["job"]
        if view["progress"] is not None:
            seen.append(view["progress"])
        if view["speed"]:
            speeds.append(view["speed"])
        time.sleep(0.15)
    job = real_box.jobs.state()["job"]
    assert job["status"] == "done" and job["progress"] == 1.0 and decodes(out)
    assert any(0.02 < p < 0.98 for p in seen), f"progress was never visible while it ran: {seen[:10]}"
    assert seen == sorted(seen), "progress only moves forward"
    assert speeds and speeds[-1].endswith("x"), speeds[-3:]
    print(f"progress samples: {len(seen)}, last speed {speeds[-1]}")


def test_cancelling_a_real_encode_stops_ffmpeg_and_leaves_no_file(real_box):
    clip = make_long_clip(real_box.work / "demo" / "src" / "long.mp4", 300)
    out = real_box.work / "demo" / "out" / "never.mp4"
    real_box.jobs.start("encode", {"input": str(clip), "output": str(out), "preset": "final", "sw": True})
    assert real_box.until(lambda: (real_box.jobs.state()["job"]["progress"] or 0) > 0.01, 60)
    assert any(str(out) in " ".join(p.info["cmdline"] or []) for p in psutil.process_iter(["cmdline"])), "ffmpeg should be running"
    started = time.time()
    real_box.jobs.cancel()
    job = real_box.finish(30)
    assert job["status"] == "cancelled" and time.time() - started < 20
    assert not out.exists()
    assert not any(str(out) in " ".join(p.info["cmdline"] or []) for p in psutil.process_iter(["cmdline"])), "no ffmpeg left behind"


def test_a_real_batch_of_proxies_through_the_job_runner(real_box):
    for name in ("a.mp4", "b.mp4"):
        make_clip(real_box.work / "proj" / "src" / name)
    job = real_box.run("proxies", project="proj")
    assert job["status"] == "done" and "2 made" in job["summary"] and job["count"] == 2
    assert all(decodes(p) for p in (real_box.work / "proj" / "proxy").glob("*.mp4"))
    overview = real_box.jobs.overview()
    assert overview["projects"][0]["proxy"]["files"] == 2
