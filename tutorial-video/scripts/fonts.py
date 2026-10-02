#!/usr/bin/env python3
"""Embed fonts in a film as base64 @font-face rules (no network needed at playback).

  python fonts.py google "Inter" 300,500,700 [--italic] [--subsets latin] [--into film.html] [--as Display]
  python fonts.py google "Archivo" "wdth,wght@62..125,100..900"      a raw axis spec (any Google axes): one variable file
  python fonts.py google "Newsreader" "opsz,wght@6..72,300..400"     with weight and width/optical-size ranges declared
  python fonts.py local "Brand" Light.woff2:300 Medium.woff2:500 Bold.woff2:700 [Italic.woff2:300i] [--into film.html]

google  downloads WOFF2 from the Google Fonts CSS API (only the subsets you ask for; latin by default) — check the licence
        (Google Fonts are OFL/Apache, fine to embed). local embeds files you were given (the brand's own font files).
--into  inserts the @font-face rules at the top of the film's first <style>; otherwise the CSS is printed.
--as    the family name to declare (default: the font's own name). Then use it: --display:'Display', sans-serif
"""
import argparse, base64, pathlib, re, sys, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument('mode', choices=['google', 'local']); ap.add_argument('family'); ap.add_argument('items', nargs='+')
ap.add_argument('--italic', action='store_true'); ap.add_argument('--subsets', default='latin'); ap.add_argument('--into'); ap.add_argument('--as', dest='alias')
A = ap.parse_args()
name = A.alias or A.family
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'   # → WOFF2 responses
rules = []


def face(b64, weight, style, fmt='woff2', urange=None, stretch=None):
    r = f"@font-face{{font-family:'{name}';src:url(data:font/{fmt};base64,{b64}) format('{fmt}');font-weight:{weight};font-style:{style};font-display:block"
    r += f';font-stretch:{stretch}' if stretch else ''
    return r + (f';unicode-range:{urange}' if urange else '') + '}'


if A.mode == 'google':
    raw = '@' in A.items[0]   # "wdth,wght@62..125,100..900": passed to Google as it is
    weights = [] if raw else [w.strip() for w in A.items[0].split(',') if w.strip()]
    axis = A.items[0].replace(' ', '') if raw else 'ital,wght@' + ';'.join([f'0,{w}' for w in weights] + ([f'1,{w}' for w in weights] if A.italic else [])) if A.italic else 'wght@' + ';'.join(weights)
    url = f'https://fonts.googleapis.com/css2?family={A.family.replace(" ", "+")}:{axis}&display=block'
    css = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=30).read().decode()
    want = {s.strip() for s in A.subsets.split(',')}
    seen = {}
    for sub, body in re.findall(r'/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{([^}]*)\}', css):
        if sub not in want:
            continue
        src = re.search(r'url\((https://[^)]+)\)', body).group(1)
        wm = re.search(r'font-weight:\s*(\d+)(?:\s+(\d+))?', body); wt = wm.group(1) if not wm.group(2) else f'{wm.group(1)} {wm.group(2)}'
        fs = re.search(r'font-stretch:\s*([^;]+)', body); stretch = fs.group(1).strip() if fs else None
        stl = re.search(r'font-style:\s*(\w+)', body).group(1)
        ur = re.search(r'unicode-range:\s*([^;]+)', body)
        key = (src, stl, sub)
        if key in seen:   # a variable font serves every weight from one file: embed it once, as a weight range
            seen[key][1] += [int(w) for w in wt.split()]; continue
        data = urllib.request.urlopen(urllib.request.Request(src, headers={'User-Agent': UA}), timeout=30).read()
        seen[key] = [data, [int(w) for w in wt.split()], stl, ur.group(1).strip() if ur else None, sub, stretch]
    for (src, stl, sub), (data, wts, stl, urange, sub, stretch) in seen.items():
        wt = str(wts[0]) if len(set(wts)) == 1 else f'{min(wts)} {max(wts)}'
        rules.append(face(base64.b64encode(data).decode(), wt, stl, 'woff2', urange, stretch))
        print(f'  {A.family} {wt} {stl} [{sub}] {len(data) / 1e3:.0f} kB' + (' (variable: one file for every weight)' if len(wts) > 1 else ''), file=sys.stderr)
    if not rules:
        sys.exit(f'no faces found for subsets {want} — check the family name / weights: {url}')
else:
    for it in A.items:
        path, _, spec = it.rpartition(':')
        if not path:
            sys.exit(f'use FILE:WEIGHT (e.g. Bold.woff2:700, Italic.woff2:300i), got {it}')
        p = pathlib.Path(path); fmt = {'.woff2': 'woff2', '.woff': 'woff', '.ttf': 'truetype', '.otf': 'opentype'}[p.suffix.lower()]
        rules.append(face(base64.b64encode(p.read_bytes()).decode(), spec.rstrip('i'), 'italic' if spec.endswith('i') else 'normal', fmt))
        print(f'  {p.name} → {name} {spec} ({p.stat().st_size / 1e3:.0f} kB)', file=sys.stderr)

css = '\n'.join(rules) + '\n'
if A.into:
    f = pathlib.Path(A.into); html = f.read_text(encoding='utf-8')
    m = re.search(r'<style\b[^>]*>', html, re.I)
    if not m:
        sys.exit('no <style> in ' + A.into)
    html = html[:m.end()] + '\n' + css + html[m.end():]
    f.write_text(html, encoding='utf-8', newline=''); print(f'inserted {len(rules)} @font-face rules into {f} — now set --display/--ui to \'{name}\'', file=sys.stderr)
else:
    sys.stdout.write(css)
