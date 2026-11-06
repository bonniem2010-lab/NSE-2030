"""Tiny JSON-over-HTTPS helper shared by the keyed data sources (standard library only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

TIMEOUT_SECONDS = 30

# (url, headers) -> parsed JSON. Sources take one of these so tests never touch the network.
HttpGet = Callable[[str, dict], object]


class SourceError(RuntimeError):
    """A data source refused or failed a request, or is not configured."""


def get_json(url: str, headers: dict, service: str, key_env: str | None = None) -> object:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        if exc.code in (401, 403) and key_env:
            raise SourceError(f"{service} HTTP {exc.code}: the API key was rejected. Check {key_env}.") from exc
        if exc.code == 402:
            raise SourceError(f"{service} HTTP 402: your plan does not include this data. {detail}") from exc
        raise SourceError(f"{service} HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SourceError(f"cannot reach {service}: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"{service} returned something that is not JSON") from exc
