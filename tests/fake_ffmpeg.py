"""A stand-in for ffmpeg, used by the tests so they run anywhere and never depend on a GPU.

It understands just enough: -version, -encoders, the 'info' form (-i FILE with no output), the self-test form
(-f lavfi ... -f null -) and a real-looking encode that writes the output file. Behaviour is steered by environment
variables: FAKE_ENCODERS (comma list), FAKE_PIXFMT, FAKE_SELFTEST_FAIL (encoder names), FAKE_FAIL_ON (a substring of
an input name that makes the encode fail after a partial output), FAKE_LOG (file that gets one line per encode),
FAKE_SLEEP (seconds an encode takes; it reports progress meanwhile, like a real one does)."""
import os
import sys
import time

args = sys.argv[1:]
encoders = os.environ.get("FAKE_ENCODERS", "libx264,libx265,aac").split(",")

if "-version" in args:
    print("ffmpeg version fake-1.0 Copyright (c) the tests")
    sys.exit(0)
if "-encoders" in args:
    print("Encoders:\n V..... = Video\n ------")
    for name in encoders:
        print(f" V....D {name:<22} fake {name}")
    sys.exit(0)
if "lavfi" in args:                                           # the self-test
    wanted = args[args.index("-c:v") + 1] if "-c:v" in args else ""
    sys.exit(1 if wanted in os.environ.get("FAKE_SELFTEST_FAIL", "").split(",") else 0)

infile = args[args.index("-i") + 1] if "-i" in args else ""
output = args[-1]
if "-i" in args and (not output or output == infile):         # ffmpeg -i FILE : prints stream info, then complains
    print(f"Input #0, mov,mp4, from '{infile}':", file=sys.stderr)
    print("  Duration: 00:00:10.00, start: 0.000000, bitrate: 1000 kb/s", file=sys.stderr)
    print(f"  Stream #0:0: Video: h264 (High), {os.environ.get('FAKE_PIXFMT', 'yuv420p')}, 1920x1080", file=sys.stderr)
    print("At least one output file must be specified", file=sys.stderr)
    sys.exit(1)

if os.environ.get("FAKE_LOG"):
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
        log.write(" ".join(args) + "\n")
with open(output, "wb") as out:
    out.write(b"fake-video-data" * 100)
if "-progress" in args:
    total = float(os.environ.get("FAKE_SLEEP", "0"))
    steps = max(1, int(total / 0.1))
    for i in range(1, steps + 1):
        with open(args[args.index("-progress") + 1], "a", encoding="utf-8") as progress:
            progress.write(f"out_time_us={int(10_000_000 * i / steps)}\nspeed=2.0x\nprogress={'end' if i == steps else 'continue'}\n")
        if total:
            time.sleep(total / steps)
fail = os.environ.get("FAKE_FAIL_ON")
if fail and fail in os.path.basename(infile):
    print("fake encode failed", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
