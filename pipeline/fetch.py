"""Polite, cached HTTP for the Maine Legislature's site."""
import hashlib, re, time, json
from pathlib import Path
import requests
from . import config

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT
_last = 0.0

def _cache_path(url: str, ext: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    d = config.RAW / "http"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.{ext}"

_MOJIBAKE = re.compile("[ÂÃ][\u0080-¿]|â€")

def _repair(text: str) -> str:
    """Undo UTF-8-read-as-Latin-1 in pages cached before the charset fix (e.g. 'TassÃ©' -> 'Tassé')."""
    if not _MOJIBAKE.search(text):
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

def get(url: str, *, binary=False, refresh=False, cache=True, timeout=60, retries=3) -> bytes | str:
    """GET with on-disk cache and a fixed delay between live requests.
    cache=False skips the cache entirely (for callers that store the body themselves)."""
    global _last
    ext = "bin" if binary else "html"
    p = _cache_path(url, ext)
    if cache and p.exists() and not refresh:
        return p.read_bytes() if binary else _repair(p.read_text(encoding="utf-8", errors="replace"))
    for attempt in range(retries):
        wait = config.REQUEST_DELAY_SECONDS - (time.time() - _last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = _session.get(url, timeout=timeout)
            _last = time.time()
            if r.status_code >= 500 and attempt < retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            r.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout):
            _last = time.time()
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    if binary:
        if cache:
            p.write_bytes(r.content)
        return r.content
    # The Legislature's JSON endpoints send UTF-8 without a charset; requests then assumes ISO-8859-1
    # for text/* and "REALTORS®" becomes "REALTORSÂ®". Use UTF-8 unless the server names a charset.
    if "charset" not in (r.headers.get("content-type") or "").lower():
        r.encoding = "utf-8"
    if cache:
        p.write_text(r.text, encoding="utf-8")
    return r.text

def get_json(url: str, refresh=False):
    txt = get(url, refresh=refresh)
    return json.loads(txt.lstrip("﻿"))
