"""URL normalization for stable IDs when RSS guid is missing."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def normalize_url(url: str) -> str:
    """Normalize URL before hashing: scheme/host lowercased, fragment stripped."""
    raw = url.strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "http").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if parsed.query:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        pairs.sort(key=lambda x: x[0])
        query = urlencode(pairs)
    else:
        query = ""
    return urlunparse((scheme, netloc, path, "", query, ""))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def article_id_from_url(url: str) -> str:
    """SHA-256 hex of normalized URL when RSS guid is missing."""
    return sha256_hex(normalize_url(url))
