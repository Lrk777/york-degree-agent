"""Term offerings from the York Courses Website (w2prod.sis.yorku.ca).

The calendar links each course to a stateless URL:
  /Apps/WebObjects/cdm.woa/wa/crsq?fa=SC&sj=MATH&cn=3330&cr=3.00&ay=2026&ss=FW
That page links to the session's schedule, which lists "Term F  Section A", "Term W ...",
"Term Y ..." (full year). We store only which terms run and how many sections.

Polite: one request at a time, >= 1 s apart, cached on disk, resumable, shardable.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

import requests

BASE = "https://w2prod.sis.yorku.ca"
_TERM_RE = re.compile(r"Term\s+(F|W|Y|S1|S2|SU|S)\s+Section\s+([A-Z0-9]+)")
_SCHED_RE = re.compile(r'href="(/Apps/WebObjects/cdm\.woa/wo/[^"]+)"[^>]*>\s*[^<]*Schedule', re.I)


def parse_schedule(html: str) -> dict:
    # The real pages write "Term F&nbsp;&nbsp;&nbsp;&nbsp;Section A": turn entities into spaces first
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;|&#160;|\xa0", " ", text)
    found = _TERM_RE.findall(text)
    terms = sorted({t for t, _ in found})
    return {"terms": terms, "sections": len({(t, s) for t, s in found})}


class OfferingsClient:
    def __init__(self, cache_dir="data/raw/w2prod", min_interval=1.0, session=None, sleep=time.sleep):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = "york-degree-agent/0.1 (student portfolio project; polite crawler)"
        self.min_interval, self._sleep, self._last = min_interval, sleep, 0.0

    def _get(self, url: str) -> str:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()
        for attempt in range(3):
            r = self.s.get(url, timeout=60)
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 500, 502, 503, 504):
                self._sleep(self.min_interval * 2 ** (attempt + 1))
                continue
            return ""
        return ""

    def course(self, fac: str, subj: str, num: str, credits: float, ay: int, ss: str = "FW") -> dict:
        key = f"{fac}/{subj}/{num}/{credits:.2f}/{ay}/{ss}"
        p = self.cache / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".txt")
        if p.exists():
            return parse_schedule(p.read_text())
        url = f"{BASE}/Apps/WebObjects/cdm.woa/wa/crsq?fa={fac}&sj={subj}&cn={num}&cr={credits:.2f}&ay={ay}&ss={ss}"
        page = self._get(url)
        m = _SCHED_RE.search(page)
        sched = self._get(BASE + m.group(1)) if m else ""
        p.write_text(sched)
        return parse_schedule(sched)


def sync_offerings(con, year: str, ay: int, shard: int = 0, shards: int = 1, client=None, limit=None) -> dict:
    from .store import upsert
    client = client or OfferingsClient()
    rows = con.execute("SELECT course_full, faculty, subject, number, credits FROM courses WHERE year=? "
                       "ORDER BY course_full", (year,)).fetchall()
    done = 0
    for i, (full, fac, subj, num, cr) in enumerate(rows):
        if i % shards != shard or not fac:
            continue
        info = client.course(fac, subj, num, cr or 0, ay)
        upsert(con, "offerings", {"ay": str(ay), "course_full": full, "terms": ",".join(info["terms"]),
                                  "sections": info["sections"], "fetched": time.strftime("%Y-%m-%d")})
        done += 1
        if done % 100 == 0:
            con.commit()
        if limit and done >= limit:
            break
    con.commit()
    return {"courses": done}
