#!/usr/bin/env python3
"""
Download every logo linked in source.html and write index.html that shows them from the images folder.
Run automatically by .github/workflows/fetch-images.yml on GitHub.

Put this script in the same folder as BIS80-entry-compare.html and run:

    python download_images.py            # images saved in an "images" folder next to the page
    python download_images.py --single   # one self-contained HTML file with the images inside it
                                          # (install Pillow first for smaller files: pip install pillow)

Output: BIS80-entry-compare-offline.html (plus the "images" folder in the default mode).
Running it again skips files that are already downloaded.
"""
import argparse, base64, io, json, mimetypes, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "source.html")
OUT = os.path.join(HERE, "index.html")
IMG_DIR = os.path.join(HERE, "images")
START = '<script id="data" type="application/json">'
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def fetch(url, dest):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True, "cached"
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            return True, "ok"
        except Exception as e:  # network errors, 404s, timeouts
            last = e
            time.sleep(1.5 * (attempt + 1))
    return False, str(last)


def shrink(path, max_px):
    """Return (bytes, mime) for embedding; resized with Pillow when available."""
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    raw = open(path, "rb").read()
    if mime == "application/pdf":
        return raw, mime
    try:
        from PIL import Image
    except ImportError:
        return raw, mime
    try:
        im = Image.open(io.BytesIO(raw))
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        if im.mode in ("RGBA", "LA", "P"):
            im.save(buf, "PNG", optimize=True)          # keep transparency
            return buf.getvalue(), "image/png"
        im.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return raw, mime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--single", action="store_true", help="embed images inside one HTML file")
    ap.add_argument("--max-px", type=int, default=1200, help="longest side when embedding (needs Pillow)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--shrink", action="store_true", help="resize downloaded images in place to --max-px (needs Pillow)")
    a = ap.parse_args()

    if not os.path.exists(SRC):
        sys.exit(f"Can't find {os.path.basename(SRC)}. Put this script in the same folder as it.")
    html = open(SRC, encoding="utf-8").read()
    i = html.index(START) + len(START)
    j = html.index("</script>", i)
    data = json.loads(html[i:j])

    os.makedirs(IMG_DIR, exist_ok=True)
    jobs = {}
    for d in data:
        for u in d["urls"]:
            name = re.sub(r"[^\w.\-]", "_", f'{d["id"]}_{u.rsplit("/", 1)[-1]}')
            jobs[u] = os.path.join(IMG_DIR, name)

    print(f"Downloading {len(jobs)} files into {IMG_DIR} ...")
    ok, failed, done = {}, {}, 0
    with ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fetch, u, p): u for u, p in jobs.items()}
        for f in as_completed(futs):
            u = futs[f]
            good, msg = f.result()
            (ok if good else failed)[u] = jobs[u] if good else msg
            done += 1
            print(f"\r  {done}/{len(jobs)}  ({len(failed)} failed)", end="", flush=True)
    print()

    if a.shrink:
        from PIL import Image
        for u, path in ok.items():
            if path.lower().endswith(".pdf"):
                continue
            try:
                im = Image.open(path)
                if max(im.size) > a.max_px:
                    im.thumbnail((a.max_px, a.max_px))
                    fmt = "PNG" if path.lower().endswith(".png") else "JPEG"
                    if fmt == "JPEG" and im.mode != "RGB":
                        im = im.convert("RGB")
                    im.save(path, fmt, **({"optimize": True} if fmt == "PNG" else {"quality": 85}))
            except Exception as e:
                print("  could not resize", os.path.basename(path), e)

    for d in data:
        new = []
        for u in d["urls"]:
            if u not in ok:
                new.append(u)                      # keep the web link as a fallback
            elif a.single:
                b, mime = shrink(ok[u], a.max_px)
                new.append(f"data:{mime};base64," + base64.b64encode(b).decode())
            else:
                new.append("images/" + os.path.basename(ok[u]))
        d["urls"] = new

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out = html[:i] + payload + html[j:]
    # data: URIs have no file extension, so teach the page to spot embedded PDFs too
    out = out.replace("const isPdf = u => /\\.pdf(\\?|$)/i.test(u);",
                      "const isPdf = u => /\\.pdf(\\?|$)/i.test(u) || u.startsWith('data:application/pdf');")
    out = out.replace("const fname = u => u.split('/').pop();",
                      "const fname = u => u.startsWith('data:') ? 'embedded file' : u.split('/').pop();")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)

    size = os.path.getsize(OUT) / 1e6
    print(f"Done: {len(ok)} downloaded, {len(failed)} failed.")
    print(f"Open {os.path.basename(OUT)} ({size:.1f} MB)" + ("" if a.single else " - keep it next to the images folder."))
    if failed:
        with open(os.path.join(HERE, "failed_downloads.txt"), "w") as f:
            for u, m in failed.items():
                f.write(f"{u}\t{m}\n")
        print("Failed links are listed in failed_downloads.txt; run the script again to retry them.")


if __name__ == "__main__":
    main()
