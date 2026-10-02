#!/usr/bin/env python3
"""Find where each line of a voiceover actually lands, so picture beats can be pinned to it.

  python vo_timing.py vo.mp3                         speech segments (start, end) in seconds
  python vo_timing.py vo.mp3 --lines script.txt      map script lines (one per line) to segments → SUBS, DUCK, line starts
  script.txt may be the exact ElevenLabs script: <break time="0.6s" /> tags, [audio tags] and # comment lines are ignored
  options: --silence -35 (dB below the loudest speech that counts as a pause)   --min-gap 0.28 (s)   --json out.json

Decoding: WAV natively; MP3/M4A/OGG/FLAC via `pip install miniaudio` (no ffmpeg needed), else ffmpeg if it is on PATH.
When the script has fewer lines than segments, the shortest pauses are merged first (a sentence with a comma can split in two).
Check the result by ear-free means too: the printed segment list should match the script's sentence count and rhythm.
"""
import argparse, json, pathlib, shutil, subprocess, sys, wave
import numpy as np

ap = argparse.ArgumentParser(); ap.add_argument('audio'); ap.add_argument('--lines'); ap.add_argument('--silence', type=float, default=-35)
ap.add_argument('--min-gap', type=float, default=0.28); ap.add_argument('--min-speech', type=float, default=0.15); ap.add_argument('--json')
A = ap.parse_args()
p = pathlib.Path(A.audio)


def decode(path):
    if path.suffix.lower() == '.wav':
        w = wave.open(str(path)); sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = np.frombuffer(w.readframes(w.getnframes()), {1: np.int8, 2: np.int16, 4: np.int32}[sw]).astype(np.float64)
        return raw.reshape(-1, ch).mean(1) / float(2 ** (8 * sw - 1)), sr
    try:
        import miniaudio
        d = miniaudio.decode_file(str(path), output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1, sample_rate=22050)
        return np.frombuffer(d.samples, np.int16).astype(np.float64) / 32768.0, 22050
    except ImportError:
        pass
    if shutil.which('ffmpeg'):
        raw = subprocess.run(['ffmpeg', '-v', 'quiet', '-i', str(path), '-ac', '1', '-ar', '22050', '-f', 's16le', '-'], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.int16).astype(np.float64) / 32768.0, 22050
    sys.exit('cannot decode ' + path.suffix + ': pip install miniaudio (or install ffmpeg), or pass a WAV')


x, sr = decode(p)
hop = int(0.02 * sr); n = len(x) // hop
env = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
thr = np.percentile(env, 95) + A.silence
voiced = env > thr
segs, i = [], 0
while i < n:   # runs of voiced frames, with short dips bridged
    if voiced[i]:
        j = i
        while j < n and (voiced[j] or voiced[j:j + int(A.min_gap / 0.02)].any()):
            j += 1
        if (j - i) * 0.02 >= A.min_speech:
            segs.append([round(i * 0.02, 2), round(j * 0.02, 2)])
        i = j
    else:
        i += 1
print(f'{p.name}: {len(x) / sr:.2f} s · threshold {thr:.1f} dB · {len(segs)} speech segments')
import re
def clean(l):   # drop ElevenLabs markup: <break time="0.6s" /> tags and [audio tags] such as [warmly]
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>|\[[^\]]*\]', ' ', l)).strip()
lines = [clean(l) for l in pathlib.Path(A.lines).read_text(encoding='utf-8').splitlines() if l.strip() and not l.strip().startswith('#')] if A.lines else None
lines = [l for l in lines if l] if lines else lines
if lines and len(segs) > len(lines):
    while len(segs) > len(lines):   # merge across the shortest pause
        k = min(range(len(segs) - 1), key=lambda q: segs[q + 1][0] - segs[q][1]); segs[k][1] = segs[k + 1][1]; del segs[k + 1]
if lines and len(segs) < len(lines):
    print(f'WARNING: {len(lines)} script lines but only {len(segs)} segments: lower --min-gap or check the take')
for k, (a, c) in enumerate(segs):
    print(f'  {k + 1:2d}  {a:6.2f} → {c:6.2f}  ({c - a:4.2f} s)' + (f'   {lines[k]}' if lines and k < len(lines) else ''))
if lines:
    m = min(len(lines), len(segs))
    print('\nconst SUBS = [ // [start, end, text] in voiceover time')
    for k in range(m):
        print(f"  [{segs[k][0]}, {segs[k][1]}, {json.dumps(lines[k], ensure_ascii=False)}],")
    print('];\n// pin each scene beat to the line that names it: ANCH = [[scriptTime, voTime], ...] with voTime from the starts above')
if segs:
    duck = []
    for a, c in segs:
        if duck and a - duck[-1][1] < 0.35: duck[-1][1] = c
        else: duck.append([a, c])
    print('const DUCK = [' + ', '.join(f'[{a}, {c}]' for a, c in duck) + '];   // paste into the timeline block: the score dips under speech')
    print('const VO_LINES = [' + ', '.join(str(a) for a, _ in segs) + '];   // line starts: land each scene\'s key move on these')
    print(f'// the voice ends at {segs[-1][1]:.2f} s: make DUR at least {segs[-1][1] + 2.5:.1f} s so the end card holds after the last word')
if A.json:
    pathlib.Path(A.json).write_text(json.dumps({'segments': segs, 'lines': lines}, indent=1), encoding='utf-8')
