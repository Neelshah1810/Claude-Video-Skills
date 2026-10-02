#!/usr/bin/env python3
"""Measure a film's score (the WAV from `verify.py film.html wav`): loudness, tonal balance, width, timing, clicks.

  python analyze.py score.wav                       whole film in 10 s blocks
  python analyze.py score.wav 0-6 6-8 8-30 30-60    named sections, in film seconds
  options: --bpm 120   --lead 0.05 (the engine's audio lead-in)   --hits 8,30,44 (moments that must carry an onset)
           --silent 6-8 (sections that are meant to be near-silent; checked against -35 dB)

Targets (see the sound section of SKILL.md); the verdict at the end checks each one:
  peak < 0.9, no clipped samples and 0 clicks · music RMS about -22 to -12 dBFS (the MP4 is normalised to -14 LUFS) · intentional silences below -35 dB
  raw energy below 80 Hz under ~45 % · A-weighted: mids (250 Hz–4 kHz) dominate, bass underneath
  air: A-weighted 4–12 kHz at least ~3 % (hats, shakers, bells); below that the score sounds dull and cheap
  width: some stereo (side/mid 0.08–0.6); fully mono sounds small, too wide falls apart on phones
  onsets sit on the 16th-note grid with a small constant offset; every listed hit shows an onset
Needs: pip install numpy
"""
import argparse, wave
import numpy as np

ap = argparse.ArgumentParser(); ap.add_argument('wav'); ap.add_argument('sections', nargs='*')
ap.add_argument('--bpm', type=float, default=120); ap.add_argument('--lead', type=float, default=0.05); ap.add_argument('--hits', default='')
ap.add_argument('--silent', nargs='*', default=[])
A = ap.parse_args()
w = wave.open(A.wav); sr, ch = w.getframerate(), w.getnchannels()
st = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float64).reshape(-1, ch) / 32768.0
x = st.mean(1)
dur = len(x) / sr
db = lambda v: 20 * np.log10(v + 1e-9)
verdict = []   # (ok, text)


def aweight(f):
    f2 = f * f
    r = 12194 ** 2 * f2 * f2 / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2)) * (f2 + 12194 ** 2))
    return (r * 1.2589) ** 2


BANDS = [('<80', 20, 80), ('80-250', 80, 250), ('250-1k', 250, 1000), ('1-4k', 1000, 4000), ('4-12k', 4000, min(12000, sr / 2 - 1))]


def spectrum(s):
    sp = np.abs(np.fft.rfft(s * np.hanning(len(s)))) ** 2; f = np.fft.rfftfreq(len(s), 1 / sr)
    raw = np.array([sp[(f >= lo) & (f < hi)].sum() for _, lo, hi in BANDS]); spa = sp * aweight(np.maximum(f, 1))
    per = np.array([spa[(f >= lo) & (f < hi)].sum() for _, lo, hi in BANDS])
    return 100 * raw / max(raw.sum(), 1e-12), 100 * per / max(per.sum(), 1e-12)


peak, clipped = np.abs(st).max(), int((np.abs(st) > 0.99).sum())
print(f'{A.wav}: {dur:.2f} s, {sr} Hz, {ch} ch · peak {peak:.3f} · clipped samples {clipped}')
print('%-13s %8s %7s   %-34s %s' % ('section (s)', 'rms dB', 'peak', 'raw energy  ' + ' '.join(b[0] for b in BANDS), 'A-weighted (what the ear hears)'))
secs = [tuple(map(float, s.split('-'))) for s in A.sections] or [(a, min(a + 10, dur - A.lead)) for a in np.arange(0, dur - A.lead - 0.5, 10)]
fmt = lambda v: ' '.join('%4.0f%%' % q for q in v)
for a, c in secs:
    s = x[int((a + A.lead) * sr):int((c + A.lead) * sr)]
    if len(s) < sr // 10:
        continue
    raw, per = spectrum(s)
    print('%5.1f-%-7.1f %8.1f %7.3f   %-34s %s' % (a, c, db(np.sqrt((s ** 2).mean())), np.abs(s).max(), fmt(raw), fmt(per)))

# whole-film balance, measured where the music actually plays (blocks louder than -30 dB)
blk = int(0.5 * sr); loud = [x[i:i + blk] for i in range(int(A.lead * sr), len(x) - blk, blk) if db(np.sqrt((x[i:i + blk] ** 2).mean())) > -30]
if loud:
    body = np.concatenate(loud); raw, per = spectrum(body); rms = db(np.sqrt((body ** 2).mean()))
    print(f'whole film (music only): rms {rms:.1f} dB · raw {fmt(raw)} · A-weighted {fmt(per)}')
    verdict += [(peak < 0.9 and clipped == 0, f'peak {peak:.2f}, {clipped} clipped (target < 0.9, 0)'),
                (-22 <= rms <= -12, f'music RMS {rms:.1f} dBFS (target about -22 to -12; the MP4 export normalises to -14 LUFS)'),
                (raw[0] <= 45, f'raw energy below 80 Hz {raw[0]:.0f} % (target ≤ 45 %; lower kick/bass if high)'),
                (per[2] + per[3] >= 55, f'A-weighted mids {per[2] + per[3]:.0f} % (target ≥ 55 %: keys, claps, melody carry it)'),
                (per[4] >= 3, f'A-weighted air 4–12 kHz {per[4]:.1f} % (target ≥ 3 %: add hats, shaker, bells, brighter attacks)')]
if ch == 2 and loud:
    L = np.concatenate([st[i:i + blk, 0] for i in range(int(A.lead * sr), len(x) - blk, blk) if db(np.sqrt((x[i:i + blk] ** 2).mean())) > -30])
    R = np.concatenate([st[i:i + blk, 1] for i in range(int(A.lead * sr), len(x) - blk, blk) if db(np.sqrt((x[i:i + blk] ** 2).mean())) > -30])
    side = np.sqrt((((L - R) / 2) ** 2).mean()) / max(np.sqrt((((L + R) / 2) ** 2).mean()), 1e-12)
    verdict.append((0.08 <= side <= 0.6, f'stereo width side/mid {side:.2f} (target 0.08–0.6: pan hats, arps and shakers a little)'))
for sec in A.silent:
    a, c = map(float, sec.split('-')); s = x[int((a + A.lead) * sr):int((c + A.lead) * sr)]
    r = db(np.sqrt((s ** 2).mean())) if len(s) else -99
    verdict.append((r < -35, f'intended silence {a:g}–{c:g} s at {r:.1f} dB (target < -35 dB)'))

# onsets (spectral flux), compared with the 16th-note grid
n, h = 1024, 256; win = np.hanning(n); prev = None; flux = []
for i in range(0, len(x) - n, h):
    m = np.abs(np.fft.rfft(x[i:i + n] * win)); flux.append(0.0 if prev is None else float(np.maximum(m - prev, 0).sum())); prev = m
flux = np.array(flux); Wn = int(0.4 * sr / h)   # adaptive threshold: 3× the local median (±0.4 s), never below 4 % of the loudest onset
thr = np.maximum(np.array([np.median(flux[max(0, i - Wn):i + Wn + 1]) for i in range(len(flux))]) * 3, flux.max() * 0.04)
peaks, gap = [], int(0.06 * sr / h)   # one onset per 60 ms: keep the strongest of a cluster
for i in range(1, len(flux) - 1):
    if flux[i] > thr[i] and flux[i] >= flux[i - 1] and flux[i] > flux[i + 1]:
        if peaks and i - peaks[-1] < gap:
            if flux[i] > flux[peaks[-1]]: peaks[-1] = i
        else: peaks.append(i)
on = np.array([(i * h + n / 2) / sr - A.lead for i in peaks])
if len(on):
    g = 60 / A.bpm / 4; off = (on + g / 2) % g - g / 2; iqr = 1000 * (np.percentile(off, 75) - np.percentile(off, 25))
    print(f'onsets {len(on)} · median offset from the 16th-note grid {1000 * np.median(off):+.0f} ms · spread (IQR) {iqr:.0f} ms')
    verdict.append((round(iqr) <= 25, f'onset spread on the {A.bpm:g} BPM grid {iqr:.0f} ms (target ≤ 25 ms; check --bpm if high)'))
for t0 in [float(v) for v in A.hits.split(',') if v.strip()]:
    near = on[np.abs(on - t0) < 0.08] if len(on) else []
    print(f'  hit at {t0:.2f} s: ' + (', '.join(f'{v:.3f}' for v in near) if len(near) else 'NO ONSET — the moment has no audible accent'))
    verdict.append((len(near) > 0, f'hit at {t0:.2f} s'))
# clicks: isolated one-sample spikes (an envelope that starts above silence). There must be none.
d2 = np.abs(x[1:-1] - (x[:-2] + x[2:]) / 2)
clicks = [i + 1 for i in np.where(d2 > 0.15)[0] if abs(x[i + 1]) > 8 * np.sqrt((np.r_[x[max(0, i - 31):i + 1], x[i + 2:i + 34]] ** 2).mean() + 1e-12)]
print(f'clicks (isolated one-sample spikes) {len(clicks)}' + (' at ' + ', '.join(f'{i / sr - A.lead:.3f} s' for i in clicks[:8]) if clicks else ' · good'))
verdict.append((not clicks, f'{len(clicks)} clicks (target 0)'))
# loudness curve in 0.5 s steps, to spot holes and spikes
hop = int(0.5 * sr); env = [db(np.sqrt((x[i:i + hop] ** 2).mean())) for i in range(int(A.lead * sr), len(x) - hop, hop)]
print('rms every 0.5 s (dB):')
for r in range(0, len(env), 20):
    print('  %5.1f s ' % (r * 0.5) + ' '.join('%4.0f' % v for v in env[r:r + 20]))
print('verdict:')
for ok, t in verdict:
    print(('  PASS  ' if ok else '  WARN  ') + t)
