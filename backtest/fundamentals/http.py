"""Versioned JSON cache and bounded, rate-limited requests."""

from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urlparse
import hashlib
import json
import logging
import os
import re
import time
import uuid

import pandas as pd
import requests

from .core import utc

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Response:
    """Original JSON plus the time this particular snapshot was observed."""

    data: object
    observed_at: pd.Timestamp


class JsonClient:
    """Sequential HTTP client; immutable cache snapshots survive refreshes."""

    _lock = Lock()
    _last_request: dict[str, float] = {}

    def __init__(self, cache: str | Path = "data/fundamentals/cache", *, refresh: bool = False, offline: bool = False, user_agent: str | None = None, retries: int = 4) -> None:
        """Configure storage and SEC identity; validate identity before SEC I/O."""
        self.cache = Path(cache)
        self.refresh = refresh
        self.offline = offline
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT", "")
        self.retries = retries
        self.session = requests.Session()
        self._refreshed: set[str] = set()

    def snapshots(self, identity: str) -> list[Response]:
        """Read immutable cached versions in observation order."""
        folder = self.cache / hashlib.sha256(identity.encode()).hexdigest()
        versions = []
        for path in sorted(folder.glob("*.json")):
            record = json.loads(path.read_text())
            versions.append(Response(record["data"], utc(record["observed_at"])))
        return sorted(versions, key=lambda r: r.observed_at)

    def store(self, identity: str, data: object) -> Response:
        """Atomically append a response, retaining its first observation time."""
        versions = self.snapshots(identity)
        # Repeated identical responses do not create new vintages.
        if versions and versions[-1].data == data:
            return versions[-1]
        observed = pd.Timestamp.now(tz="UTC")
        folder = self.cache / hashlib.sha256(identity.encode()).hexdigest()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{observed.value}-{uuid.uuid4().hex}.json"
        temporary = target.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps({"observed_at": observed.isoformat(), "data": data}, allow_nan=False))
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return Response(data, observed)

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None, as_of: pd.Timestamp | None = None, interval: float = 0.1) -> Response:
        """Fetch or replay JSON; vendor replay never selects a future snapshot."""
        params = params or {}
        public_params = {k: v for k, v in params.items() if k.lower() not in ("apikey", "api_key", "api-key", "token")}
        identity = url + json.dumps(public_params, sort_keys=True)
        versions = self.snapshots(identity)
        if versions and (not self.refresh or identity in self._refreshed) or self.offline:
            eligible = [v for v in versions if as_of is None or v.observed_at <= as_of]
            if eligible:
                return eligible[-1]
            raise ValueError("no cached snapshot available at the requested cutoff")
        host = urlparse(url).hostname or ""
        request_headers = dict(headers or {})
        if host in ("sec.gov", "www.sec.gov", "data.sec.gov"):
            if not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", self.user_agent):
                raise ValueError("set SEC_USER_AGENT to an application name and contact email")
            request_headers["User-Agent"] = self.user_agent
            host = "sec.gov"
        else:
            request_headers.setdefault("User-Agent", "PITFundamentals/1.0")
        interval = max(0.1, interval)
        for attempt in range(self.retries + 1):
            with self._lock:
                delay = self._last_request.get(host, 0) + interval - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self._last_request[host] = time.monotonic()
            try:
                response = self.session.get(url, params=params, headers=request_headers, timeout=(10, 60))
            except requests.RequestException:
                if attempt == self.retries:
                    raise RuntimeError(f"{host}: request failed after retries") from None
                time.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.retries:
                    raise RuntimeError(f"{host}: HTTP {response.status_code} after retries")
                retry = response.headers.get("Retry-After", "")
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        delay = (utc(parsedate_to_datetime(retry)) - pd.Timestamp.now(tz="UTC")).total_seconds()
                    except (ValueError, TypeError):
                        delay = 2 ** attempt
                if delay > 60:
                    raise RuntimeError(f"{host}: Retry-After exceeds retry budget")
                time.sleep(max(0.1, delay))
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"{host}: HTTP {response.status_code}; check key/plan/access")
            data = response.json()
            if isinstance(data, dict) and any(k in data for k in ("Error Message", "Information", "Note", "error")):
                raise RuntimeError(f"{host}: provider returned an error or quota response")
            result = self.store(identity, data)
            self._refreshed.add(identity)
            if as_of is not None and result.observed_at > as_of:
                eligible = [v for v in versions if v.observed_at <= as_of]
                if eligible:
                    return eligible[-1]
                raise ValueError("new vendor response is later than as_of; archived for later decisions")
            return result
        raise RuntimeError("retry budget exhausted")
