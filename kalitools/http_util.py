"""Hardened HTTP helpers used by the scraper code paths.

Implements:

* A single ``User-Agent`` identifying the project with a version + repo URL.
* ``robots.txt`` awareness (cached per host).
* Exponential backoff with jitter on 5xx / connection errors.
* Per-request timeout and a global circuit breaker per host.
* Honours ``KALITOOLS_OFFLINE=1`` — returns ``None`` without making a
  request so the rest of the CLI keeps working on air-gapped hosts.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from . import __version__

logger = logging.getLogger("kalitools.http")

USER_AGENT = (
    f"kali-tools-manager/{__version__} "
    "(+https://github.com/MushroomCyber/Kali-Tools-Manager)"
)

DEFAULT_TIMEOUT = 10.0
MAX_ATTEMPTS = 4
INITIAL_BACKOFF = 1.5
MAX_BACKOFF = 15.0
CIRCUIT_FAIL_THRESHOLD = 5  # per-host consecutive failures
CIRCUIT_COOLDOWN = 120.0  # seconds to back off the host


@dataclass
class _HostState:
    fails: int = 0
    open_until: float = 0.0
    robots: RobotFileParser | None = None
    robots_fetched: bool = False
    lock: Lock = field(default_factory=Lock)


_HOSTS: dict[str, _HostState] = {}
_HOSTS_LOCK = Lock()


def offline() -> bool:
    """True if the user asked us not to make network calls."""
    return bool(os.environ.get("KALITOOLS_OFFLINE"))


def _host_state(host: str) -> _HostState:
    with _HOSTS_LOCK:
        state = _HOSTS.get(host)
        if state is None:
            state = _HostState()
            _HOSTS[host] = state
        return state


def _fetch_robots(host_url: str, state: _HostState) -> None:
    if state.robots_fetched or requests is None:
        return
    parser = RobotFileParser()
    try:
        url = f"{host_url}/robots.txt"
        resp = requests.get(url, timeout=5, headers={"User-Agent": USER_AGENT})
        if resp.status_code == 200:
            parser.parse(resp.text.splitlines())
        else:
            parser.parse([])
    except Exception as exc:  # pragma: no cover - network failure
        logger.debug("robots.txt fetch failed for %s: %s", host_url, exc)
        parser.parse([])
    state.robots = parser
    state.robots_fetched = True


def allowed_by_robots(url: str) -> bool:
    """Return True if robots.txt permits fetching *url* for our User-Agent."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    state = _host_state(parsed.netloc)
    host_url = f"{parsed.scheme}://{parsed.netloc}"
    with state.lock:
        _fetch_robots(host_url, state)
    if state.robots is None:
        return True
    try:
        return state.robots.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def polite_get(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> Any | None:
    """GET *url* with retries, backoff, robots.txt check and circuit breaker.

    Returns a ``requests.Response`` on success, or ``None`` on offline mode,
    circuit-open, robots-disallow, or persistent failure.
    """
    if requests is None:
        return None
    if offline():
        logger.debug("offline mode; skipping GET %s", url)
        return None

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        logger.warning("refusing non-HTTP(S) scheme: %s", parsed.scheme)
        return None
    if not parsed.netloc:
        return None
    state = _host_state(parsed.netloc)
    now = time.time()
    if state.open_until > now:
        logger.debug("circuit open for %s until %.0f", parsed.netloc, state.open_until - now)
        return None

    if not allowed_by_robots(url):
        logger.info("robots.txt disallows %s", url)
        return None

    merged_headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        merged_headers.update(headers)

    backoff = INITIAL_BACKOFF
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, timeout=timeout, headers=merged_headers)
            if resp.status_code < 500 and resp.status_code != 429:
                with state.lock:
                    state.fails = 0
                return resp
            logger.debug("GET %s -> %s (attempt %d)", url, resp.status_code, attempt)
        except requests.RequestException as exc:
            last_exc = exc
            logger.debug("GET %s failed (attempt %d): %s", url, attempt, exc)
        if attempt < max_attempts:
            sleep_for = min(MAX_BACKOFF, backoff + random.random())
            time.sleep(sleep_for)
            backoff = min(MAX_BACKOFF, backoff * 2)

    with state.lock:
        state.fails += 1
        if state.fails >= CIRCUIT_FAIL_THRESHOLD:
            state.open_until = time.time() + CIRCUIT_COOLDOWN
            logger.warning(
                "circuit breaker OPEN for %s (%d consecutive failures) for %ds",
                parsed.netloc, state.fails, int(CIRCUIT_COOLDOWN),
            )
    if last_exc is not None:
        logger.info("giving up on %s after %d attempts: %s", url, max_attempts, last_exc)
    return None
