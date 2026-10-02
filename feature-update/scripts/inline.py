#!/usr/bin/env python3
"""Make a film self-contained: inline every local asset it references.

  python inline.py film.src.html film.html

Inlines (as data URIs or inline text):
  <img|audio|video|source|image|track|link rel=icon|embed|object> src / href / poster / data
  <link rel="stylesheet" href="x.css">  → <style> (its own url(...) refs resolve from the CSS file's folder)
  <script src="x.js"></script>         → inline <script>
  url(...) inside <style> blocks and style="" attributes (fonts, backgrounds)
Kept as they are: http(s)://, //, data:, #fragment and <use href> references (put SVG symbols inline instead).
Prints what was inlined and the final size; warns above 25 MB.
"""
import base64, mimetypes, pathlib, re, sys

MIME = {'.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.otf': 'font/otf', '.svg': 'image/svg+xml', '.png': 'image/png',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.avif': 'image/avif', '.gif': 'image/gif', '.mp3': 'audio/mpeg',
        '.m4a': 'audio/mp4', '.wav': 'audio/wav', '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm', '.json': 'application/json',
        '.vtt': 'text/vtt', '.ico': 'image/x-icon'}
if len(sys.argv) != 3:
    sys.exit(__doc__)
src, dst = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2])
base = src.parent
html = src.read_text(encoding='utf-8')
done = []


def is_local(ref):
    ref = ref.strip()
    return bool(ref) and not re.match(r'^(?:[a-z][a-z0-9+.-]*:|//|#)', ref, re.I)


def data_uri(path):
    p = path.resolve()
    if not p.exists():
        print('  MISSING:', p)
        return None
    mt = MIME.get(p.suffix.lower()) or mimetypes.guess_type(str(p))[0] or 'application/octet-stream'
    done.append(f'{p.name} ({p.stat().st_size / 1e3:.0f} kB)')
    return f'data:{mt};base64,' + base64.b64encode(p.read_bytes()).decode()


def clean(ref):
    return ref.split('#')[0].split('?')[0]


def css_urls(css, folder):
    def rep(m):
        ref = m.group(2)
        if not is_local(ref):
            return m.group(0)
        u = data_uri(folder / clean(ref))
        return f'url({u})' if u else m.group(0)
    return re.sub(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)', rep, css)


def link_tag(m):
    tag = m.group(0)
    href = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', tag, re.I)
    if not href or not is_local(href.group(1)):
        return tag
    if re.search(r'\brel\s*=\s*["\'][^"\']*stylesheet', tag, re.I):
        p = (base / clean(href.group(1))).resolve()
        done.append(p.name)
        return '<style>\n' + css_urls(p.read_text(encoding='utf-8'), p.parent) + '\n</style>'
    u = data_uri(base / clean(href.group(1)))   # icons and other links
    return tag.replace(href.group(1), u) if u else tag


def script_tag(m):
    ref = m.group(1)
    if not is_local(ref):
        return m.group(0)
    p = (base / clean(ref)).resolve()
    done.append(p.name)
    return '<script>\n' + p.read_text(encoding='utf-8').replace('</script', '<\\/script') + '\n</script>'


def media_attr(m):
    full, name, q, ref = m.group(0), m.group(2), m.group(3), m.group(4)
    if not is_local(ref):
        return full
    u = data_uri(base / clean(ref))
    return full.replace(f'{name}={q}{ref}{q}', f'{name}={q}{u}{q}') if u else full


KEEP = []   # HTML comments and inline script bodies are never scanned for assets (a comment that mentions <audio src> is not an asset)


def stash(text):
    KEEP.append(text); return f'\x00KEEP{len(KEEP) - 1}\x00'


def keep(m):
    return stash(m.group(0))


html = re.sub(r'<!--.*?-->', keep, html, flags=re.S)
html = re.sub(r'<link\b[^>]*>', link_tag, html, flags=re.I)
html = re.sub(r'<script\b[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*>\s*</script>', script_tag, html, flags=re.I)
html = re.sub(r'(<style\b[^>]*>)(.*?)(</style>)', lambda m: m.group(1) + css_urls(m.group(2), base) + m.group(3), html, flags=re.S | re.I)
html = re.sub(r'\bstyle\s*=\s*"([^"]*url\([^"]*)"', lambda m: 'style="' + css_urls(m.group(1), base).replace('"', "'") + '"', html, flags=re.I)
html = re.sub(r'(<script\b[^>]*>)(.*?)(</script>)', lambda m: m.group(1) + stash(m.group(2)) + m.group(3), html, flags=re.S | re.I)
for _ in range(3):   # a tag can carry several refs (e.g. <video poster src>); inlined ones start with data:, so this converges
    html = re.sub(r'<(img|audio|video|source|image|track|embed|object)\b[^>]*?\b(src|href|xlink:href|poster|data)\s*=\s*(["\'])((?!data:|https?:|//|#)[^"\']+)\3',
                  media_attr, html, flags=re.I)
for _ in range(2):   # restore protected blocks (a kept script may itself contain a kept marker)
    html = re.sub(r'\x00KEEP(\d+)\x00', lambda m: KEEP[int(m.group(1))], html)
dst.write_text(html, encoding='utf-8', newline='')
size = dst.stat().st_size / 1e6
print('inlined:', ', '.join(done) if done else 'nothing (already self-contained)')
print(f'wrote {dst} · {size:.2f} MB' + ('  WARNING: over 25 MB, compress images/audio first' if size > 25 else ''))
