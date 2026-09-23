"""Small, cached reader for TypeSafe's public API-status page.

This is consulted only after a Jev request already failed.  It must never
turn a failed-open draft check into a second source of delay or failure.
"""

import html
import logging
import re
import threading
import time

import httpx


log = logging.getLogger("jev-send-guard")

STATUS_SECTIONS_URL = "https://status.typesafe.ai/sections"
STATUS_TIMEOUT_SEC = 2.0
CACHE_SECONDS = 60.0

_cache_lock = threading.Lock()
_last_checked_at = None
_last_issue = None


def _parse_api_issue(page):
    """Return a human-readable issue for api.typesafe.ai, if any.

    Typesafe's Better Stack page places the resource's status icon shortly
    before its name.  Only a non-operational API status counts: an unrelated
    console incident must not be presented as a Jev outage.
    """
    page = html.unescape(page)
    api_match = re.search(r"api\.typesafe\.ai", page, flags=re.IGNORECASE)
    if api_match is None:
        return None
    # The closest preceding icon belongs to this resource; an earlier
    # degraded Console icon must not taint the following API row.
    status_matches = list(
        re.finditer(r'aria-label="(?P<status>[^"]+)"', page[:api_match.start()], flags=re.IGNORECASE)
    )
    if not status_matches:
        return None
    status = status_matches[-1].group("status").strip()
    if status.lower() == "operational":
        return None
    return f"TypeSafe API is {status.lower()}"


def get_api_issue():
    """Return the current TypeSafe API incident text, or ``None``.

    Failure to fetch or parse a status page is deliberately indistinguishable
    from an unknown status.  The user already sees the ordinary "Not checked"
    state in that case, and should not receive a false backend-outage claim.
    """
    global _last_checked_at, _last_issue
    now = time.monotonic()
    with _cache_lock:
        if _last_checked_at is not None and now - _last_checked_at < CACHE_SECONDS:
            return _last_issue

    try:
        with httpx.Client(
            timeout=STATUS_TIMEOUT_SEC, trust_env=False, follow_redirects=True
        ) as client:
            response = client.get(STATUS_SECTIONS_URL)
            response.raise_for_status()
        issue = _parse_api_issue(response.text)
    except httpx.HTTPError as exc:
        log.debug("TypeSafe status lookup failed: %s", exc)
        issue = None

    with _cache_lock:
        _last_checked_at = now
        _last_issue = issue
    return issue
