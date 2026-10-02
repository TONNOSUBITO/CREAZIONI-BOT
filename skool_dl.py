#!/usr/bin/env python3
"""Scarica testi e PDF di una classroom Skool a cui sei iscritto. Nessun LLM.

Uso:
  export SKOOL_COOKIE='auth_token=...; ...'   # copiato dal tuo browser loggato
  python skool_dl.py aiworkshop [cartella_output]

Idempotente: i file già scaricati vengono saltati. Per ogni lezione salva anche
il JSON grezzo (_raw.json), così nulla va perso se lo schema di Skool cambia.
"""
import json, os, re, sys, time
from pathlib import Path
import httpx

BASE = "https://www.skool.com"
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def slug(s):
    return re.sub(r"[^\w\-. ]+", "_", s or "senza-titolo").strip()[:80]


def get(client, url, **kw):
    for attempt in range(4):
        r = client.get(url, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def next_data(client, url):
    m = NEXT_DATA.search(get(client, url).text)
    if not m:
        sys.exit(f"__NEXT_DATA__ non trovato in {url}: cookie scaduto o pagina cambiata?")
    return json.loads(m.group(1))


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def lesson_text(data, lesson_id):
    """Cerca nel JSON il modulo con id == lesson_id; ritorna (titolo, testo, urls)."""
    for d in walk(data):
        if d.get("id") == lesson_id and isinstance(d.get("metadata"), dict):
            md = d["metadata"]
            urls = []
            res = md.get("resources")
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except ValueError:
                    res = None
            for r in res or []:
                if isinstance(r, dict) and (r.get("file_id") or r.get("link")):
                    urls.append((r.get("title") or "allegato", r.get("link") or r.get("file_id")))
            return md.get("title") or d.get("name"), md.get("desc") or "", urls
    return None, "", []


def main():
    community = sys.argv[1]
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "skool_out")
    cookie = os.environ.get("SKOOL_COOKIE") or sys.exit("Imposta SKOOL_COOKIE")
    headers = {"Cookie": cookie, "User-Agent": "Mozilla/5.0"}
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30) as c:
        root = f"{BASE}/{community}/classroom"
        courses = sorted(set(re.findall(rf"/{community}/classroom/([0-9a-f]{{8}})", get(c, root).text)))
        print(f"{len(courses)} corsi")
        for cid in courses:
            curl = f"{root}/{cid}"
            cdata = next_data(c, curl)
            ctitle = next((d["metadata"].get("title") for d in walk(cdata)
                           if d.get("name") == cid and isinstance(d.get("metadata"), dict)), cid)
            lessons = sorted(set(re.findall(r'"id":"([0-9a-f]{32})"', json.dumps(cdata))))
            cdir = out / slug(ctitle)
            for lid in lessons:
                ldir = cdir / lid
                if (ldir / "_raw.json").exists():
                    continue
                try:
                    ldata = next_data(c, f"{curl}?md={lid}")
                except httpx.HTTPError as e:
                    print("skip", lid, e)
                    continue
                title, text, files = lesson_text(ldata, lid)
                if title is None:  # id non è una lezione (es. utente/corso)
                    continue
                ldir.mkdir(parents=True, exist_ok=True)
                (ldir / "lezione.md").write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
                for name, ref in files:
                    url = ref if str(ref).startswith("http") else f"https://assets.skool.com/f/{ref}"
                    try:
                        data = get(c, url).content
                        ext = ".pdf" if data[:4] == b"%PDF" else ""
                        (ldir / (slug(name) + ("" if slug(name).endswith(ext) else ext))).write_bytes(data)
                    except httpx.HTTPError as e:
                        print("allegato fallito", name, e)
                (ldir / "_raw.json").write_text(json.dumps(ldata), encoding="utf-8")
                print("ok", ctitle, "/", title)
                time.sleep(0.5)


if __name__ == "__main__":
    main()
