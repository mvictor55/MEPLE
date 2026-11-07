"""Polite, cached HTTP for the Maine Legislature's site."""
import hashlib, time, json
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

def get(url: str, *, binary=False, refresh=False, timeout=60) -> bytes | str:
    """GET with on-disk cache and a fixed delay between live requests."""
    global _last
    ext = "bin" if binary else "html"
    p = _cache_path(url, ext)
    if p.exists() and not refresh:
        return p.read_bytes() if binary else p.read_text(errors="replace")
    wait = config.REQUEST_DELAY_SECONDS - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    r = _session.get(url, timeout=timeout)
    _last = time.time()
    r.raise_for_status()
    if binary:
        p.write_bytes(r.content)
        return r.content
    r.encoding = r.encoding or "utf-8"
    p.write_text(r.text)
    return r.text

def get_json(url: str, refresh=False):
    txt = get(url, refresh=refresh)
    return json.loads(txt)
