"""Client for York's public calendar API (Kuali Catalog at york.kuali.co).

Endpoints (public, no key; found from the calendar page's own requests):
  /api/v1/catalog/public/catalogs              one catalog per calendar year
  /api/v1/catalog/programs/{catalog}           program list (+ specializations)
  /api/v1/catalog/program/{catalog}/{pid}      program detail with requirement HTML
  /api/v1/catalog/courses/{catalog}            course list (~10k rows)
  /api/v1/catalog/course/{catalog}/{pid}       course detail with description

Politeness and safety:
  * one request at a time, at most one per `min_interval` seconds (default 1.0)
  * retries with exponential backoff on 429/5xx; gives up after 4 tries
  * every response is cached on disk by URL; a re-run only fetches what is missing,
    so loads are idempotent and a crashed run resumes where it stopped
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Optional

import requests

BASE = "https://york.kuali.co/api/v1/catalog"
UA = "york-degree-agent/0.1 (student portfolio project; polite crawler; contact via GitHub)"


class KualiClient:
    def __init__(self, cache_dir: str | Path = "data/raw/kuali", min_interval: float = 1.0,
                 session: Optional[requests.Session] = None, sleep: Callable[[float], None] = time.sleep):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = UA
        self._last = 0.0
        self._sleep = sleep
        self.stats = {"network": 0, "cache": 0, "retries": 0}

    def _path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()[:24]
        return self.cache / f"{h}.json"

    def cached(self, url: str):
        p = self._path(url)
        return json.loads(p.read_text())["body"] if p.exists() else None

    def get(self, url: str, refresh: bool = False):
        p = self._path(url)
        if p.exists() and not refresh:
            self.stats["cache"] += 1
            return json.loads(p.read_text())["body"]
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            r = self.s.get(url, timeout=60)
            self.stats["network"] += 1
            if r.status_code == 200:
                body = r.json()
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps({"url": url, "fetched": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                           "body": body}))
                tmp.replace(p)                     # atomic: never leaves half-written cache files
                return body
            if r.status_code in (429, 500, 502, 503, 504):
                self.stats["retries"] += 1
                self._sleep(self.min_interval * (2 ** (attempt + 1)))
                continue
            r.raise_for_status()
        raise RuntimeError(f"gave up after retries: {url}")

    # --- endpoints
    def catalogs(self, refresh=False):
        return self.get(f"{BASE}/public/catalogs", refresh)

    def programs(self, catalog_id):
        return self.get(f"{BASE}/programs/{catalog_id}")

    def program(self, catalog_id, pid):
        return self.get(f"{BASE}/program/{catalog_id}/{pid}")

    def courses(self, catalog_id):
        return self.get(f"{BASE}/courses/{catalog_id}")

    def course(self, catalog_id, pid):
        return self.get(f"{BASE}/course/{catalog_id}/{pid}")


def undergrad_catalogs(catalogs: list[dict]) -> dict[str, dict]:
    """Map 'FW25/26' -> catalog, skipping admin copies and duplicates."""
    out = {}
    for c in catalogs:
        t = c.get("title", "").strip()
        if "admin" in t.lower() or "FW" not in t:
            continue
        year = t.split()[1] if t.startswith("York FW") else t
        out[year] = c
    return out
