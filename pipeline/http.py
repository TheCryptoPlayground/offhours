"""A small HTTP client: per-host pacing, retries on 429/5xx, and readable errors."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import requests

log = logging.getLogger("offhours")


class HTTPError(Exception):
    def __init__(self, status: int, url: str, body: str = ""):
        self.status, self.url, self.body = status, url, body
        hint = ""
        if status in (403, 451):
            hint = " (this usually means the venue blocks the runner's country; see README)"
        super().__init__(f"HTTP {status} for {url}{hint}: {body[:200]}")


class HTTP:
    def __init__(self, user_agent: str = "offhours-tracker/0.1"):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        self._last: dict[str, float] = {}
        self.calls = 0

    def get(self, url: str, params: dict | None = None, headers: dict | None = None,
            pace: float = 0.0, retries: int = 6, timeout: float = 30.0):
        host = urlsplit(url).netloc
        error: Exception | None = None
        for attempt in range(retries):
            wait = self._last.get(host, 0.0) + pace - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()
            self.calls += 1
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=timeout)
            except requests.RequestException as e:  # network trouble: back off and retry
                error = e
                time.sleep(min(60, 2 ** attempt))
                continue
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError as e:
                    raise HTTPError(200, r.url, f"not JSON: {r.text[:120]}") from e
            if r.status_code in (418, 429) or r.status_code >= 500:
                ra = r.headers.get("Retry-After", "")
                delay = float(ra) if ra.replace(".", "", 1).isdigit() else min(90, 3 * 2 ** attempt)
                log.warning("%s returned %s; retrying in %.0fs", host, r.status_code, delay)
                time.sleep(delay)
                error = HTTPError(r.status_code, r.url, r.text)
                continue
            raise HTTPError(r.status_code, r.url, r.text)
        if isinstance(error, HTTPError):
            raise error
        raise HTTPError(-1, url, f"gave up after {retries} attempts: {error}")
