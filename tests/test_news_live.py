"""Offline fixtures for pinned publisher news and live-mode boundaries."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
import os
import sys
import tempfile
import types
import unittest

from app.config import Settings
from app.market_worker import current_news
from app.news import NEWS_SCHEMA, assess_live, validate_news
from app.news_sources import PublisherFeedError, collect_publisher_rss
from app.news_worker import once

UTC = timezone.utc


def rss(title, link, date, description="Bitcoin macro update"):
    return ("<?xml version='1.0'?><rss><channel><item><title>" + title +
            "</title><link>" + link + "</link><pubDate>" + date +
            "</pubDate><description>" + description + "</description></item></channel></rss>").encode()


class Response:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def getcode(self): return 200
    def read(self, amount): return self.body


class Opener:
    def __init__(self, bodies): self.bodies = iter(bodies)
    def open(self, request, timeout): return Response(next(self.bodies))


class StrictSchemaTests(unittest.TestCase):
    def test_openai_strict_json_schema_has_no_unsupported_keywords(self):
        banned={"maxLength","minLength","maxItems","minItems","pattern","format","minimum","maximum","allOf"}
        def walk(obj):
            if isinstance(obj,dict):
                for key,value in obj.items():
                    self.assertNotIn(key,banned)
                    walk(value)
            elif isinstance(obj,list):
                for item in obj:walk(item)
        walk(NEWS_SCHEMA)

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)
        self.date = "Thu, 06 Nov 2026 00:00:00 +0000"
        self.bodies = [
            rss("Bitcoin update", "https://www.coindesk.com/a", self.date),
            rss("BTC update", "https://cointelegraph.com/b", self.date),
            rss("Macro update", "https://www.theblock.co/c", self.date),
        ]

    def test_pinned_feeds_produce_receipts_without_network(self):
        with patch("app.news_sources._public_dns", return_value=True):
            rows = collect_publisher_rss(now=self.now, opener=Opener(self.bodies))
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["publisher_host"] for r in rows}, {"www.coindesk.com", "cointelegraph.com", "www.theblock.co"})
        self.assertTrue(all(r["factual_match"] and len(r["content_sha256"]) == 64 for r in rows))

    def test_two_of_three_publishers_allow_bounded_news_input(self):
        class BadThird(Opener):
            def open(self, request, timeout):
                if request.full_url.endswith("/rss.xml"):
                    raise HTTPError(request.full_url, 503, "unavailable", {}, None)
                return super().open(request, timeout)
        with patch("app.news_sources._public_dns", return_value=True):
            rows=collect_publisher_rss(now=self.now,opener=BadThird(self.bodies[:2]))
        self.assertEqual({row["publisher_host"] for row in rows},
                         {"www.coindesk.com","cointelegraph.com"})

    def test_only_one_publisher_fails_closed(self):
        class One(Opener):
            def open(self,request,timeout):
                if "cointelegraph" in request.full_url or request.full_url.endswith("/rss.xml"):
                    raise HTTPError(request.full_url,503,"unavailable",{},None)
                return Response(self.bodies[0])
            def __init__(self,bodies): self.bodies=bodies
        with patch("app.news_sources._public_dns",return_value=True):
            with self.assertRaises(PublisherFeedError):
                collect_publisher_rss(now=self.now,opener=One(self.bodies))

    def test_busy_feed_does_not_exclude_other_publishers(self):
        bodies=[]
        for host in ("www.coindesk.com","cointelegraph.com","www.theblock.co"):
            items="".join("<item><title>Bitcoin "+str(i)+"</title><link>https://"+host+"/"+str(i)+"</link><pubDate>"+self.date+"</pubDate></item>" for i in range(14))
            bodies.append(("<?xml version='1.0'?><rss><channel>"+items+"</channel></rss>").encode())
        with patch("app.news_sources._public_dns",return_value=True):
            rows=collect_publisher_rss(now=self.now,opener=Opener(bodies))
        self.assertEqual(len(rows),8)
        self.assertEqual(len({row["publisher_host"] for row in rows}),3)

    def test_malicious_or_bad_feed_fails_closed(self):
        bad = b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><rss/>"
        with patch("app.news_sources._public_dns", return_value=True):
            with self.assertRaises(PublisherFeedError):
                collect_publisher_rss(now=self.now, opener=Opener([bad, bad, bad]))

    def test_redirect_is_refused(self):
        class Redirect:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 302, "redirect", {}, None)
        with patch("app.news_sources._public_dns", return_value=True):
            with self.assertRaises(PublisherFeedError):
                collect_publisher_rss(now=self.now, opener=Redirect())

    def test_false_link_and_stale_date_fail_closed(self):
        stale = "Tue, 04 Nov 2026 00:00:00 +0000"
        bodies = [rss("Bitcoin", "https://evil.example/x", self.date), rss("Bitcoin", "https://cointelegraph.com/b", stale), rss("Macro", "https://www.theblock.co/c", self.date)]
        with patch("app.news_sources._public_dns", return_value=True):
            with self.assertRaises(PublisherFeedError):
                collect_publisher_rss(now=self.now, opener=Opener(bodies))

    def test_two_source_clear_one_source_block_and_mismatched_citation(self):
        now = self.now
        rows = [
            {"publisher_host":"www.coindesk.com", "title":"A", "url":"https://www.coindesk.com/a", "http_status":200, "fetched_at_utc":now.isoformat(), "published_at_utc":now.isoformat(), "content_sha256":"a"*64, "factual_match":True},
            {"publisher_host":"cointelegraph.com", "title":"B", "url":"https://cointelegraph.com/b", "http_status":200, "fetched_at_utc":now.isoformat(), "published_at_utc":now.isoformat(), "content_sha256":"b"*64, "factual_match":True},
        ]
        clear = {"state":"clear", "summary":"ok", "valid_for_minutes":5, "sources":[{"title":"A","url":rows[0]["url"]},{"title":"B","url":rows[1]["url"]}]}
        self.assertEqual(validate_news(clear, "x", {}, now, rows).state, "clear")
        block = {**clear, "state":"block", "sources":[clear["sources"][0]]}
        self.assertEqual(validate_news(block, "x", {}, now, rows).state, "block")
        self.assertEqual(validate_news({**clear, "sources":[{"title":"A","url":"https://www.coindesk.com/not-a"}]}, "x", {}, now, rows).state, "unknown")
        self.assertEqual(validate_news({"state":"clear", "summary":"x", "valid_for_minutes":5, "sources":[]}, "x", {}, now, []).state, "unknown")

    def test_stored_forgery_and_expired_receipts_are_unknown(self):
        now = self.now
        class DB:
            def __init__(self, rows, expiry): self.rows, self.expiry = rows, expiry
            def latest_news(self): return ("clear", "x", self.expiry, self.rows, now)
        forged = [{"url":"https://www.coindesk.com/a"}]
        self.assertEqual(current_news(DB(forged, now + timedelta(minutes=5)), now, "live")[0], "unknown")
        stale = [{"publisher_host":"www.coindesk.com", "title":"A", "url":"https://www.coindesk.com/a", "http_status":200, "fetched_at_utc":(now-timedelta(hours=3)).isoformat(), "published_at_utc":now.isoformat(), "content_sha256":"a"*64, "factual_match":True}]
        self.assertEqual(current_news(DB(stale, now + timedelta(minutes=5)), now, "live")[0], "unknown")


class LiveWorkerTests(unittest.TestCase):
    def test_live_mode_requires_opt_in_and_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "key"; key.write_text("x" * 40)
            env = {"DRY_RUN":"true", "LIVE_TRADING_ENABLED":"false", "NEWS_MODE":"live", "ALLOW_BILLABLE_NEWS":"I_ACCEPT_BILLING", "OPENAI_API_KEY_FILE":str(key)}
            with patch.dict(os.environ, env, clear=True): self.assertEqual(Settings.load().news_mode, "live")

    def test_attempt_cap_is_24_before_assessment(self):
        class DB:
            last = None
            def __init__(self, role): DB.last = self
            def reserve_news_attempt(self, limit): self.limit = limit; return None
            def record_news(self, *args): self.recorded = args
            def close(self): pass
        cfg = type("Cfg", (), {"news_mode":"live"})()
        with patch("app.news_worker.Store", DB):
            self.assertEqual(once(cfg), "unknown")
        self.assertEqual(DB.last.limit, 24)

    def test_live_call_has_no_web_tool_and_uses_fixture_receipts(self):
        now = datetime.now(UTC)
        rows = [{"publisher_host":"www.coindesk.com", "title":"A", "description":"BTC", "url":"https://www.coindesk.com/a", "http_status":200, "fetched_at_utc":now.isoformat(), "published_at_utc":now.isoformat(), "content_sha256":"a"*64, "factual_match":True}, {"publisher_host":"cointelegraph.com", "title":"B", "description":"BTC", "url":"https://cointelegraph.com/b", "http_status":200, "fetched_at_utc":now.isoformat(), "published_at_utc":now.isoformat(), "content_sha256":"b"*64, "factual_match":True}]
        class FakeResponses:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return type("R", (), {"status":"completed", "output_text":'{"state":"clear","summary":"ok","sources":[{"title":"A","url":"https://www.coindesk.com/a"},{"title":"B","url":"https://cointelegraph.com/b"}],"valid_for_minutes":5}', "usage":None})()
        fake = type("Client", (), {"responses":FakeResponses()})()
        with patch("app.news.collect_publisher_rss", return_value=rows), patch("app.news.read_secret", return_value="x"*40), patch.dict(sys.modules, {"openai":types.SimpleNamespace(OpenAI=lambda **kwargs: fake)}):
            out = assess_live()
        self.assertEqual(out.state, "clear")
        self.assertNotIn("tools", fake.responses.kwargs)
