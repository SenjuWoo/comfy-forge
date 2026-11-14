"""Live ACE-Step 1.5 check: a real backend renders a real song.

TESTS/test_music.py proves the graph matches the template. This proves the
installation: it asks a running ComfyUI for a short instrumental, decodes the audio,
checks that it has the length asked for and is not silence, then renders the same
seed again and checks both takes decode to identical samples (what makes
`generate --from <take>.json` a real way back to a take).

Exit codes:
  0  PASS        audio came back with the right length and signal, and the same seed repeats
  1  FAIL        the backend answered and something is wrong
  3  UNVERIFIED  no backend, weights not installed, or no ffmpeg to decode with

Usage:
    python TESTS/integration_music.py [--url URL] [--variant aio] [--seconds 30]

Without --url the backend is found the way music.py finds it (COMFYUI_URL, then
port 8188, then SwarmUI's 7820-7832).
"""
import argparse
import array
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUSIC = os.path.join(REPO, "TOOLS", "music.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("COMFYUI_URL"))
    ap.add_argument("--variant", default="aio")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    url = ["--url", args.url] if args.url else []
    ready = subprocess.run([sys.executable, MUSIC, "check", "--variant", args.variant] + url,
                           capture_output=True, text=True)
    print(ready.stdout.strip())
    if ready.returncode == 3:
        print("UNVERIFIED: no ComfyUI backend")
        return 3
    if ready.returncode != 0:
        # Missing weights are an environment gap, not a defect in the tool.
        print("UNVERIFIED: the backend is up but this variant is not installed")
        return 3
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("UNVERIFIED: ffmpeg is needed to decode the result")
        return 3

    with tempfile.TemporaryDirectory() as tmp:
        def render(folder):
            run = subprocess.run(
                [sys.executable, MUSIC, "generate", "--variant", args.variant] + url +
                ["--tags", "calm lo-fi hip hop, mellow rhodes piano, soft drums, vinyl warmth",
                 "--instrumental", "--duration", str(args.seconds), "--bpm", "82", "--key", "A minor",
                 "--seed", "1234", "--format", "flac", "--out", os.path.join(tmp, folder),
                 "--timeout", str(args.timeout)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(run.stdout.strip())
            if run.returncode != 0:
                print(run.stderr.strip())
                return None
            path = json.loads(run.stdout.strip().splitlines()[-1])["files"][0]
            pcm = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
                                 capture_output=True)
            if pcm.returncode != 0:
                print("FAIL ffmpeg could not decode the file:", pcm.stderr.decode(errors="replace")[:300])
                return None
            return pcm.stdout

        first = render("a")
        if first is None:
            print("FAIL generate did not finish")
            return 1
        samples = array.array("h")
        samples.frombytes(first[: len(first) // 2 * 2])
        seconds = len(samples) / 44100
        rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) / 32768
        peak = max((abs(s) for s in samples), default=0) / 32768
        print(f"ok   decoded     {seconds:.1f}s, rms {rms:.4f}, peak {peak:.3f}")
        if abs(seconds - args.seconds) > 1.5:
            print(f"FAIL length {seconds:.1f}s is not the {args.seconds}s asked for")
            return 1
        if rms < 0.005 or peak < 0.05:
            print("FAIL the audio is silent or nearly so")
            return 1
        second = render("b")
        if second is None:
            print("FAIL the repeat render did not finish")
            return 1
        if hashlib.sha256(first).hexdigest() != hashlib.sha256(second).hexdigest():
            print("FAIL the same seed and settings produced different audio")
            return 1
        print("ok   repeat      same seed, identical samples")
    print("INTEGRATION: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
