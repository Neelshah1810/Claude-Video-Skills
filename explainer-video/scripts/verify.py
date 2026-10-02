#!/usr/bin/env python3
"""Verify, render and export a motion film in headless Chrome (via the DevTools Protocol).

Every run (except `start`) first sweeps the whole timeline (seek every 0.05 s) and reports any exception
thrown inside seek()/render(), plus page errors, then checks that every font the film uses is embedded.

  python verify.py film.html check                      sweep + fonts + a frame every 0.1 s scanned for blank frames and static holds
  python verify.py film.html sweep                      only the sweep and the font check
  python verify.py film.html frames 0 60 0.5            PNG frames + 3-column contact sheets (12 frames per sheet)
  python verify.py film.html at 3.2 7.95 12             frames at exact times (+ a sheet)
  python verify.py film.html start                      the player's start screen (poster frame + overlay), not ?record
  python verify.py film.html poster [t]                 poster.jpg at window.poster (or t): the thumbnail for the MP4 and README
  python verify.py film.html wav [rate]                 offline render of the score → score.wav (default 48000 Hz)
  python verify.py film.html export [fps]               every frame as PNG → frames/f_00000.png … (for your own ffmpeg)
  python verify.py film.html eval "js" [t]               evaluate an expression in the ?record page (after seek(t)) and print the JSON result,
                                                        e.g. eval "getComputedStyle(document.querySelector('#cT')).fontSize" 9.5
  python verify.py film.html mp4 [fps]                  the finished MP4 in one step: score + frames piped to ffmpeg,
                                                        loudness-normalised, H.264, +faststart (default 30 fps)

Options:
  --out DIR        output folder (default ./verify_out)
  --size WxH       stage size; by default it is read from the film (window.stageSize, else #stage)
  --scale N        device pixel ratio for frames/mp4/poster (2 → 3840×2160 from a 1920×1080 stage)
  --mp4 PATH       where `mp4` writes (default: next to the film, same name, .mp4)
  --vo FILE        a voiceover (mp3/wav) to mix with the score in `mp4`; it starts at film time 0
  --audio MODE     score (default) | vo (voiceover only) | none (silent MP4)
  --crf N          H.264 quality for `mp4` (default 18; lower is better and bigger)
  --lufs N         loudness target for `mp4` (default -14, right for web, YouTube and social)
Needs:  pip install websocket-client pillow numpy · Chrome, Chromium or Edge (set CHROME=/path if not found) · ffmpeg for `mp4`

Warnings this script prints are the usual tells of an unfinished film. Treat each one as a bug unless it is deliberate:
  fonts not embedded    the film renders in a fallback font on any machine without that font installed
  blank frames          a frame that is one flat colour (an empty first frame makes a blank thumbnail)
  static holds          nothing on screen changes for 3 s or more (keep holds alive with a slow push)
"""
import argparse, base64, io, json, os, pathlib, shutil, socket, subprocess, sys, tempfile, time, urllib.request

try:
    import websocket
    from PIL import Image, ImageDraw, ImageStat
except ImportError:
    sys.exit('pip install websocket-client pillow numpy')


def find_chrome():
    cands = [os.environ.get('CHROME'), shutil.which('google-chrome'), shutil.which('google-chrome-stable'), shutil.which('chromium'),
             shutil.which('chromium-browser'), shutil.which('chrome'), shutil.which('msedge'),
             r'C:\Program Files\Google\Chrome\Application\chrome.exe', r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
             r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
             '/Applications/Chromium.app/Contents/MacOS/Chromium', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    for c in cands:
        if c and os.path.exists(c):
            return c
    sys.exit('Chrome not found: set CHROME=/path/to/chrome')


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


ap = argparse.ArgumentParser(description='Verify and export a motion film.')
ap.add_argument('film'); ap.add_argument('cmd', choices=['check', 'sweep', 'frames', 'at', 'start', 'poster', 'wav', 'export', 'mp4', 'eval']); ap.add_argument('args', nargs='*')
ap.add_argument('--out', default='verify_out'); ap.add_argument('--size'); ap.add_argument('--scale', type=float, default=1); ap.add_argument('--port', type=int, default=0)
ap.add_argument('--mp4'); ap.add_argument('--vo'); ap.add_argument('--audio', choices=['score', 'vo', 'none'], default='score')
ap.add_argument('--crf', type=int, default=18); ap.add_argument('--lufs', type=float, default=-14)
A = ap.parse_args()
film = pathlib.Path(A.film).resolve(); out = pathlib.Path(A.out); out.mkdir(parents=True, exist_ok=True)
if not film.exists():
    sys.exit(f'no such film: {film}')
if A.cmd == 'mp4' and not shutil.which('ffmpeg'):
    sys.exit('ffmpeg not found: install it (brew install ffmpeg · apt install ffmpeg · winget install ffmpeg)')
if A.audio == 'vo' and not A.vo:
    sys.exit('--audio vo needs --vo FILE')
port = A.port or free_port()
proc = subprocess.Popen([find_chrome(), '--headless=new', '--disable-gpu', '--hide-scrollbars', '--mute-audio', '--force-color-profile=srgb',
                         f'--remote-debugging-port={port}', '--remote-allow-origins=*', f'--user-data-dir={tempfile.mkdtemp(prefix="vf_")}',
                         '--window-size=1920,1080', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
errors, warnings, mid, ws = [], [], [0], None
W, H = 1920, 1080


def call(method, params=None):
    mid[0] += 1; ws.send(json.dumps({'id': mid[0], 'method': method, 'params': params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get('method') == 'Runtime.exceptionThrown':
            d = m['params']['exceptionDetails']; errors.append('page: ' + d.get('exception', {}).get('description', d.get('text', '?'))[:300])
        elif m.get('method') == 'Runtime.consoleAPICalled' and m['params'].get('type') == 'error':
            errors.append('console.error: ' + ' '.join(str(a.get('value', a.get('description', ''))) for a in m['params'].get('args', []))[:300])
        if m.get('id') == mid[0]:
            if 'error' in m:
                errors.append(f'{method}: ' + m['error'].get('message', '?'))
            return m.get('result', {})


def js(expr, wait=True):
    """Evaluate in the page. Exceptions thrown by seek()/render() land HERE, not in page error events."""
    r = call('Runtime.evaluate', {'expression': expr, 'awaitPromise': wait, 'returnByValue': True})
    if 'exceptionDetails' in r:
        errors.append('eval: ' + r['exceptionDetails'].get('exception', {}).get('description', '?')[:300])
    return r.get('result', {}).get('value')


def metrics(w, h, scale=1):
    call('Emulation.setDeviceMetricsOverride', {'width': w, 'height': h, 'deviceScaleFactor': scale, 'mobile': False})


def load(record=True):
    call('Page.navigate', {'url': film.as_uri() + ('?record' if record else '')})
    time.sleep(0.2)
    for i in range(400):
        if js("!!(window.videoReady && document.body && document.body.classList.contains('ready'))", False):
            js('window.videoReady'); return
        if i > 12 and any(e.startswith('page:') for e in errors):   # a script error during load: the film will never become ready
            sys.exit('the film threw while loading:\n  ' + '\n  '.join(errors))
        time.sleep(0.15)
    sys.exit('film never became ready (window.videoReady / body.ready): check page errors ' + str(errors))


def settle():
    js('new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))')


def shot(fmt='png', q=None):
    p = {'format': fmt}
    if q:
        p['quality'] = q
    return base64.b64decode(call('Page.captureScreenshot', p)['data'])


def frame(t):
    js(f'window.seek({t})'); settle()
    return Image.open(io.BytesIO(shot())).convert('RGB')


def flat(im):
    """A frame that is (almost) one flat colour: no content to look at."""
    st = ImageStat.Stat(im.convert('L').resize((192, 108)))
    return st.stddev[0] < 1.2


def report_flat(ts, step=0.5):
    if not ts:
        return
    runs, a, prev = [], ts[0], ts[0]
    for t in ts[1:] + [None]:
        if t is None or t - prev > step * 1.05:   # flagged frames one scan step apart form one run
            runs.append((a, prev)); a = t
        if t is not None:
            prev = t
    desc = ', '.join(f'{x:.2f} s' if x == y else f'{x:.2f}–{y:.2f} s' for x, y in runs)
    warnings.append(f'blank frames (one flat colour) at {desc}' + ('  ← the FIRST frame is blank: the thumbnail will be empty' if ts[0] == 0 else ''))


def report_static(items, step):
    if step > 0.5 or len(items) < 3:
        return
    import numpy as np
    run0, last = None, None
    for t, im in items:
        a = np.asarray(im.convert('L').resize((192, 108)), dtype=np.float32)
        if last is not None and np.abs(a - last).mean() < 0.06:
            run0 = t - step if run0 is None else run0
        else:
            if run0 is not None and t - step - run0 >= 3:
                warnings.append(f'static hold {run0:.2f}–{t - step:.2f} s: nothing moves (keep holds alive with a 1–3 % push)')
            run0 = None
        last = a
    if run0 is not None and items[-1][0] - run0 >= 3:
        warnings.append(f'static hold {run0:.2f}–{items[-1][0]:.2f} s: nothing moves (fine only for a final end-card hold)')


def sheets(items, tag):
    tw = 640 if W >= H else 300; th = int(tw * H / W)
    for si in range(0, len(items), 12):
        chunk = items[si:si + 12]; rows = (len(chunk) + 2) // 3
        sheet = Image.new('RGB', (3 * (tw + 8) + 8, rows * (th + 26) + 8), (40, 40, 40)); d = ImageDraw.Draw(sheet)
        for k, (t, im) in enumerate(chunk):
            x, y = 8 + k % 3 * (tw + 8), 8 + k // 3 * (th + 26)
            sheet.paste(im.resize((tw, th)), (x, y)); d.text((x + 4, y + th + 6), f't = {t:.2f}', fill='white')
        fn = out / f'sheet_{tag}_{si // 12:02d}.png'; sheet.save(fn); print('sheet', fn)


FONT_CHECK = r"""(() => {
  const norm = s => s.replace(/["']/g, '').trim().toLowerCase();
  const GENERIC = /^(serif|sans-serif|monospace|cursive|fantasy|system-ui|ui-sans-serif|ui-serif|ui-monospace|ui-rounded|-apple-system|blinkmacsystemfont|segoe ui|menlo|consolas|monaco|courier new|georgia|arial|helvetica|helvetica neue|times new roman|arial black|inherit|initial)$/;
  const faces = [...document.fonts];
  const wOk = (f, w) => { const p = String(f.weight).split(/\s+/).map(Number); return p.length > 1 ? w >= p[0] && w <= p[1] : p[0] === w; };
  const need = new Map(), glyphs = new Map();
  const ranges = f => String(f.unicodeRange || 'U+0-10FFFF').split(',').map(r => { r = r.trim().replace(/^u\+/i, '');
    if (r.includes('?')) return [parseInt(r.replace(/\?/g, '0'), 16), parseInt(r.replace(/\?/g, 'F'), 16)];
    const [a, b] = r.split('-'); return [parseInt(a, 16), parseInt(b || a, 16)]; });
  const stage = document.getElementById('stage') || document.body;
  for (const e of stage.querySelectorAll('*')) {
    const own = [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('') + (e.dataset ? e.dataset.type || '' : '');
    if (!own.trim()) continue;   // only elements that render text (data-type: text typed in later)
    const cs = getComputedStyle(e), fam = norm(cs.fontFamily.split(',')[0]);
    if (!fam || GENERIC.test(fam)) continue;
    const fr = faces.filter(f => norm(f.family) === fam).flatMap(ranges);
    if (fr.length) for (const ch of own) { const cp = ch.codePointAt(0); if (cp > 32 && !fr.some(([lo, hi]) => cp >= lo && cp <= hi)) glyphs.set(ch, `${ch} U+${cp.toString(16).toUpperCase().padStart(4, '0')} in ${fam}`); }
    const key = fam + '|' + (+cs.fontWeight) + '|' + (cs.fontStyle === 'italic' ? 'italic' : 'normal');
    if (!need.has(key)) need.set(key, (e.textContent || '').trim().slice(0, 24));
  }
  const missing = [], weights = [];
  for (const [key, sample] of need) {
    const [fam, w, style] = key.split('|');
    const fams = faces.filter(f => norm(f.family) === fam);
    if (!fams.length) { missing.push(fam); continue; }
    if (!fams.some(f => wOk(f, +w) && (f.style === style || (style === 'normal' && f.style !== 'italic')))) weights.push(`${fam} ${w}${style === 'italic' ? ' italic' : ''} ("${sample}")`);
  }
  return { faces: faces.length, missing: [...new Set(missing)], weights: [...new Set(weights)], glyphs: [...glyphs.values()], failed: faces.filter(f => f.status === 'error').map(f => f.family) };
})()"""


def checks(dur):
    bad = js(f"(() => {{ const bad = []; for (let i = 0; i <= {int(dur * 20)}; i++) {{ try {{ window.seek(i / 20); }} catch (e) {{ bad.push((i / 20).toFixed(2) + ' s: ' + e.message); if (bad.length > 12) break; }} }} return bad; }})()", False)
    print(f'timeline sweep (0–{dur:.2f} s every 0.05 s):', 'no exceptions' if not bad else '\n  ' + '\n  '.join(bad))
    if bad:
        errors.append(f'{len(bad)} exception(s) during the sweep')
    fc = js(FONT_CHECK, False) or {}
    if fc.get('missing'):
        warnings.append('fonts not embedded (fallback font on other machines): ' + ', '.join(fc['missing']) + ' → embed them with fonts.py')
    if fc.get('weights'):
        warnings.append('font weights/styles used but not embedded (the browser fakes them): ' + '; '.join(fc['weights'][:8]))
    if fc.get('glyphs'):
        warnings.append('characters outside the embedded font subset (they render in a system font): ' + '; '.join(fc['glyphs'][:10]) + ' → draw them as SVG, or embed a subset that has them (fonts.py --subsets)')
    if fc.get('failed'):
        errors.append('font faces that failed to load: ' + ', '.join(fc['failed']))
    print(f"fonts: {fc.get('faces', 0)} embedded face(s)" + ('' if fc.get('missing') or fc.get('weights') or fc.get('glyphs') else ' · every font, weight and glyph the film uses is embedded'))
    js('window.seek(0)')


def render_wav(rate=48000):
    load(True)
    b64 = js(f"window.renderWav({{ rate: {rate} }}).then(b => b.arrayBuffer()).then(a => {{ const u = new Uint8Array(a); let s = ''; for (let i = 0; i < u.length; i += 32768) s += String.fromCharCode.apply(null, u.subarray(i, i + 32768)); return btoa(s); }})")
    if not b64:
        sys.exit('renderWav() failed: ' + str(errors))
    fn = out / 'score.wav'; fn.write_bytes(base64.b64decode(b64)); print('score', fn, f'({fn.stat().st_size / 1e6:.1f} MB)')
    return fn


try:
    for _ in range(100):
        try:
            tab = next(t for t in json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2).read()) if t['type'] == 'page'); break
        except Exception:
            time.sleep(0.25)
    else:
        sys.exit('could not connect to headless Chrome')
    ws = websocket.create_connection(tab['webSocketDebuggerUrl'], suppress_origin=True, timeout=900)
    call('Runtime.enable'); call('Page.enable')
    metrics(1920, 1080)
    load(A.cmd != 'start')
    dur = float(js('window.duration', False) or 0)
    if A.size:
        W, H = map(int, A.size.lower().split('x'))
    else:
        W, H = js("(() => { if (window.stageSize) return window.stageSize; const s = document.getElementById('stage'); return s ? [s.offsetWidth, s.offsetHeight] : [1920, 1080]; })()", False) or [1920, 1080]
        W, H = int(W), int(H)
    scale = A.scale if A.cmd in ('frames', 'at', 'poster', 'export', 'mp4') else 1
    metrics(W, H, scale); js("dispatchEvent(new Event('resize'))"); settle()
    print(f'{film.name}: {dur:.2f} s · stage {W}×{H}' + (f' · ×{scale:g} → {int(W * scale)}×{int(H * scale)}' if scale != 1 else ''))
    if A.cmd == 'eval':
        if len(A.args) > 1:
            js(f'window.seek({float(A.args[1])})'); settle()
        print(json.dumps(js(A.args[0], True), indent=1, ensure_ascii=False))
    elif A.cmd == 'start':
        time.sleep(1.0); fn = out / 'start.png'; Image.open(io.BytesIO(shot())).save(fn); print('start screen', fn)
    else:
        checks(dur)

    if A.cmd == 'check':
        ts = [round(i * 0.1, 2) for i in range(int(round(dur / 0.1)) + 1)]   # 0.1 s: catches a blank frame right after a cut
        items = [(t, frame(t)) for t in ts]
        report_flat([t for t, im in items if flat(im)], 0.1); report_static(items[::5], 0.5)
        print(f'scanned {len(ts)} frames every 0.1 s for blank frames (static holds judged every 0.5 s)')
    elif A.cmd == 'frames':
        a, c, step = map(float, A.args); ts = [round(a + i * step, 3) for i in range(int(round((c - a) / step)) + 1)]
        items = []
        for t in ts:
            im = frame(t); im.save(out / f'f_{t:07.2f}.png'); items.append((t, im))
        report_flat([t for t, im in items if flat(im)], step); report_static(items, step)
        sheets(items, f'{a:g}-{c:g}')
    elif A.cmd == 'at':
        items = []
        for t in map(float, A.args):
            im = frame(t); im.save(out / f'f_{t:07.2f}.png'); items.append((t, im))
        report_flat([t for t, im in items if flat(im)])
        sheets(items, 'at')
    elif A.cmd == 'poster':
        t = float(A.args[0]) if A.args else (lambda v: float(v) if v is not None else max(0, dur - 1))(js('window.poster', False))
        fn = out / 'poster.jpg'; frame(t).save(fn, quality=92); print(f'poster at {t:.2f} s → {fn}')
    elif A.cmd == 'wav':
        render_wav(int(A.args[0]) if A.args else 48000)
    elif A.cmd == 'export':
        fps = int(A.args[0]) if A.args else 30; fd = out / 'frames'; fd.mkdir(exist_ok=True); n = int(round(dur * fps)); fl = []
        for i in range(n):
            im = frame(i / fps); im.save(fd / f'f_{i:05d}.png')
            if flat(im):
                fl.append(round(i / fps, 2))
            if i % (fps * 5) == 0:
                print(f'  {i}/{n}')
        report_flat(fl, 1 / fps)
        print(f'{n} frames → {fd}')
    elif A.cmd == 'mp4':
        fps = int(A.args[0]) if A.args else 30; n = int(round(dur * fps))
        dst = pathlib.Path(A.mp4).resolve() if A.mp4 else film.with_suffix('.mp4')
        poster_t = (lambda v: float(v) if v is not None else max(0, dur - 1))(js('window.poster', False))
        wav = None
        if A.audio == 'score':
            metrics(W, H, 1); wav = render_wav(48000); load(True); metrics(W, H, scale); js("dispatchEvent(new Event('resize'))"); settle()
        cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'image2pipe', '-framerate', str(fps), '-c:v', 'png', '-i', '-']
        loud = f'loudnorm=I={A.lufs}:TP=-1.5:LRA=11'
        if A.audio == 'score' and A.vo:
            cmd += ['-i', str(wav), '-i', str(pathlib.Path(A.vo).resolve()), '-filter_complex',
                    f'[1:a]atrim=start=0.05,asetpts=PTS-STARTPTS[m];[2:a]asetpts=PTS-STARTPTS[v];[m][v]amix=inputs=2:normalize=0:duration=first,{loud},aresample=48000[a]',
                    '-map', '0:v', '-map', '[a]']
        elif A.audio == 'score':
            cmd += ['-i', str(wav), '-af', f'atrim=start=0.05,asetpts=PTS-STARTPTS,{loud},aresample=48000', '-map', '0:v', '-map', '1:a']
        elif A.audio == 'vo':
            cmd += ['-i', str(pathlib.Path(A.vo).resolve()), '-af', f'{loud},aresample=48000', '-map', '0:v', '-map', '1:a']
        cmd += ['-c:v', 'libx264', '-preset', 'slow', '-crf', str(A.crf), '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-r', str(fps), '-movflags', '+faststart']
        if A.audio != 'none':
            cmd += ['-c:a', 'aac', '-b:a', '192k', '-t', f'{n / fps:.3f}']
        cmd += [str(dst)]
        ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        fl, t0 = [], time.time()
        for i in range(n):
            js(f'window.seek({i / fps})'); settle(); png = shot()
            ff.stdin.write(png)
            if i % 6 == 0 and flat(Image.open(io.BytesIO(png))):
                fl.append(round(i / fps, 2))
            if i % (fps * 5) == 0:
                print(f'  frame {i}/{n}  ({time.time() - t0:.0f} s)')
        ff.stdin.close(); rc = ff.wait()
        if rc:
            sys.exit(f'ffmpeg failed (exit {rc})')
        report_flat(fl, 6 / fps)
        poster = dst.with_suffix('.jpg'); frame(poster_t).save(poster, quality=92)
        print(f'MP4 → {dst} ({dst.stat().st_size / 1e6:.1f} MB, {n} frames at {fps} fps, {int(W * scale)}×{int(H * scale)})  ·  poster → {poster}')
finally:
    for w_ in warnings:
        print('WARNING:', w_)
    print('errors:', 'none' if not errors else '\n  ' + '\n  '.join(errors))
    if ws:
        ws.close()
    proc.kill()
