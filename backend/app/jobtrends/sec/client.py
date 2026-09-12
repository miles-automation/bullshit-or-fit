import fcntl
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

MAX_RESPONSE_BYTES = 30_000_000


class SecClient:
    def __init__(
        self,
        contact: str,
        cache_dir: Path,
        transport: httpx.BaseTransport | None = None,
        interval_seconds: float = 0.5,
    ) -> None:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", contact):
            raise ValueError(
                "SEC_CONTACT_EMAIL must contain an authorized contact email"
            )
        if not math.isfinite(interval_seconds) or interval_seconds < 0.25:
            raise ValueError("SEC requests must be spaced by at least 0.25 seconds")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.interval_seconds = interval_seconds
        self.http = httpx.Client(
            headers={"User-Agent": f"SparkSwarm BullshitOrFit SEC Research {contact}"},
            timeout=httpx.Timeout(60, connect=15),
            transport=transport,
            follow_redirects=False,
        )
        self.requests = 0
        self.cache_hits = 0

    def __enter__(self) -> "SecClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.http.close()

    def _throttle(self) -> None:
        with (self.cache_dir / "request.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            lock.seek(0)
            now = time.monotonic()
            try:
                last = float(lock.read())
            except ValueError:
                last = now
            if not math.isfinite(last):
                last = now
            delay = min(
                self.interval_seconds, max(0, self.interval_seconds - (now - last))
            )
            if delay > 0:
                time.sleep(delay)
            lock.seek(0)
            lock.truncate()
            lock.write(str(time.monotonic()))
            lock.flush()

    def fetch(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in {
            "www.sec.gov",
            "data.sec.gov",
        }:
            raise ValueError("SEC client only accepts official HTTPS SEC URLs")
        for attempt in range(4):
            self._throttle()
            self.requests += 1
            try:
                with self.http.stream("GET", url) as response:
                    response.raise_for_status()
                    result = bytearray()
                    for part in response.iter_bytes():
                        result.extend(part)
                        if len(result) > MAX_RESPONSE_BYTES:
                            raise ValueError("SEC response exceeds 30 MB limit")
                    return bytes(result)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                retry_after = 0.0
                if isinstance(exc, httpx.HTTPStatusError):
                    if exc.response.status_code not in {429, 500, 502, 503, 504}:
                        raise
                    try:
                        retry_after = float(
                            exc.response.headers.get("Retry-After", "0")
                        )
                    except ValueError:
                        retry_after = 0.0
                if attempt == 3:
                    raise
                time.sleep(min(120, max(2 ** (attempt + 1), retry_after)))
        raise RuntimeError("SEC retries exhausted")

    def json(self, url: str, ttl_seconds: int = 86_400) -> dict[str, Any]:
        cache = self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        if cache.exists() and time.time() - cache.stat().st_mtime < ttl_seconds:
            try:
                payload = json.loads(cache.read_bytes())
                if isinstance(payload, dict):
                    self.cache_hits += 1
                    return payload
            except (ValueError, OSError):
                pass
        raw = self.fetch(url)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("SEC submissions response must be an object")
        temporary = cache.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(raw)
        temporary.replace(cache)
        return payload
