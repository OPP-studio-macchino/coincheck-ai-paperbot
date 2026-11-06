"""Pinned, bounded publisher RSS evidence for the paper news gate.

This module intentionally collects RSS entries, not article bodies.  URLs are
fixed here and no URL supplied by a model or caller is ever fetched.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
import ipaddress
import re
import socket
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree as ET


MAX_BYTES = 256 * 1024
MAX_ITEMS = 8
MAX_AGE = timedelta(hours=24)
TRUSTED_FEEDS = {
    "www.coindesk.com": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph.com": "https://cointelegraph.com/rss",
    "www.theblock.co": "https://www.theblock.co/rss.xml",
}
TRUSTED_ARTICLE_HOSTS = {
    "www.coindesk.com": {"www.coindesk.com"},
    "cointelegraph.com": {"cointelegraph.com", "www.cointelegraph.com"},
    "www.theblock.co": {"www.theblock.co", "theblock.co"},
}
_RELEVANT = re.compile(r"\b(btc|bitcoin|crypto|cryptocurrency|digital asset|fed|fomc|inflation|rate|yen|jpy|macro)\b", re.I)
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class PublisherFeedError(RuntimeError):
    """A pinned publisher feed was unavailable or did not meet policy."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive time")
    return value.astimezone(timezone.utc)


def _public_dns(host: str) -> bool:
    """Reject numeric and private/internal resolution before opening a feed."""
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    if host.endswith((".localhost", ".local", ".internal")) or host == "localhost":
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    seen = {entry[4][0] for entry in addresses}
    if not seen:
        return False
    for raw in seen:
        ip = ipaddress.ip_address(raw)
        if not ip.is_global:
            return False
    return True


def canonical_article_url(value: str, publisher_host: str) -> str | None:
    """Accept only canonical HTTPS links on the publisher's known article hosts."""
    if not isinstance(value, str) or len(value) > 500 or any(c in value for c in "\r\n\x00"):
        return None
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                parsed.port not in (None, 443) or parsed.query or parsed.fragment or
                host not in TRUSTED_ARTICLE_HOSTS.get(publisher_host, set())):
            return None
        return urlunsplit(("https", host, parsed.path or "/", "", ""))
    except ValueError:
        return None


def _text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _read_feed(url: str, host: str, opener=None) -> bytes:
    if not _public_dns(host):
        raise PublisherFeedError("publisher_dns_invalid")
    request = Request(url, headers={"User-Agent": "paper-news-rss/1.0", "Accept": "application/rss+xml, application/xml, text/xml"})
    try:
        response = (opener or build_opener(_NoRedirect())).open(request, timeout=8)
        with response:
            if response.getcode() != 200:
                raise PublisherFeedError("publisher_http_invalid")
            body = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        # urllib HTTPError may have no response body (notably a denied 3xx).
        # Never mask the security rejection with an exception from close().
        if getattr(exc, "fp", None) is not None:
            exc.close()
        raise PublisherFeedError("publisher_redirect_or_http_error") from exc
    except (OSError, ValueError) as exc:
        raise PublisherFeedError("publisher_unreachable") from exc
    if len(body) > MAX_BYTES:
        raise PublisherFeedError("publisher_feed_too_large")
    if re.search(br"<!\s*(DOCTYPE|ENTITY)\b", body, re.I):
        raise PublisherFeedError("publisher_xml_entities_forbidden")
    return body


def _published(item: ET.Element) -> datetime:
    raw = _text(item.find("pubDate"))
    if not raw:
        raise PublisherFeedError("publisher_date_missing")
    try:
        return _utc(parsedate_to_datetime(raw))
    except (TypeError, ValueError, IndexError) as exc:
        raise PublisherFeedError("publisher_date_invalid") from exc


def _entries(body: bytes, host: str, fetched: datetime) -> list[dict]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PublisherFeedError("publisher_xml_invalid") from exc
    records: list[dict] = []
    saw_relevant = False
    for item in root.findall(".//item"):
        title = _text(item.find("title"))[:400]
        url = canonical_article_url(_text(item.find("link")), host)
        description = _text(item.find("description"))[:400]
        published = _published(item)
        if not _RELEVANT.search(" ".join((title, description))):
            continue
        saw_relevant = True
        if not title or not url or fetched - published > MAX_AGE or published > fetched + timedelta(seconds=5):
            continue
        # Hash the actual entry bytes when available, otherwise the fetched feed.
        entry_bytes = ET.tostring(item, encoding="utf-8") or body
        records.append({
            "publisher_host": host,
            "url": url,
            "title": title,
            "description": description,
            "http_status": 200,
            "fetched_at_utc": fetched.isoformat(),
            "published_at_utc": published.isoformat(),
            "content_sha256": sha256(entry_bytes).hexdigest(),
            "factual_match": True,
        })
    if saw_relevant and not records:
        raise PublisherFeedError("publisher_relevant_entries_stale_or_invalid")
    return records


def collect_publisher_rss(*, now: datetime | None = None, opener=None) -> list[dict]:
    """Collect independently verified records from two or more pinned publishers."""
    fetched = _utc(now or datetime.now(timezone.utc))
    per_publisher: list[list[dict]] = []
    for host, url in TRUSTED_FEEDS.items():
        try:
            body = _read_feed(url, host, opener)
            entries = _entries(body, host, fetched)
        except PublisherFeedError:
            continue  # No unsafe alternate URL or model-provided source.
        if entries:
            per_publisher.append(entries)
    if len(per_publisher) < 2:
        raise PublisherFeedError("independent_publisher_quorum_missing")
    # Round-robin ensures a busy source cannot exhaust all eight citations.
    records: list[dict] = []
    while len(records) < MAX_ITEMS and any(per_publisher):
        for entries in per_publisher:
            if entries and len(records) < MAX_ITEMS:
                records.append(entries.pop(0))
    return records


def valid_receipts(receipts: list[dict] | object, now: datetime | None = None) -> list[dict]:
    """Validate persisted server receipts; never treat a bare URL as evidence."""
    current = _utc(now or datetime.now(timezone.utc))
    valid: list[dict] = []
    if not isinstance(receipts, list):
        return valid
    for receipt in receipts[:MAX_ITEMS]:
        if not isinstance(receipt, dict):
            continue
        try:
            host = receipt["publisher_host"]
            url = canonical_article_url(receipt["url"], host)
            title = receipt["title"]
            fetched = _utc(datetime.fromisoformat(receipt["fetched_at_utc"]))
            published = _utc(datetime.fromisoformat(receipt["published_at_utc"]))
            digest = receipt["content_sha256"]
            if (not isinstance(title, str) or not title or len(title) > 400 or not url or
                    receipt.get("http_status") != 200 or receipt.get("factual_match") is not True or
                    not isinstance(digest, str) or not _SHA.fullmatch(digest) or
                    fetched > current + timedelta(seconds=5) or published > current + timedelta(seconds=5) or
                    current - fetched > timedelta(hours=2) or current - published > MAX_AGE):
                continue
            valid.append({"publisher_host": host, "url": url, "title": title,
                          "http_status": 200, "fetched_at_utc": fetched.isoformat(),
                          "published_at_utc": published.isoformat(), "content_sha256": digest,
                          "factual_match": True})
        except (KeyError, TypeError, ValueError):
            continue
    return valid
