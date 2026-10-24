"""Offline checks for the seven-day crawler and engagement ranking."""

import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import crawler
import requests


class Response:
    def __init__(self, text="", payload=None):
        self.text = text
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


class CrawlerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 16, tzinfo=timezone.utc)
        self.calls = []

    def fetch(self, url, params=None):
        self.calls.append((url, params))
        if url.endswith("/thoi-su"):
            return Response('<article class="item-news"><h3 class="title-news">'
                            '<a href="/recent-1111111.html">Recent</a></h3></article>'
                            '<article class="item-news"><h3 class="title-news">'
                            '<a href="/quiet-3333333.html">Quiet</a></h3></article>'
                            '<aside><a href="/promo-4444444.html">Promoted</a></aside>')
        if url.endswith("/thoi-su-p2"):
            return Response('<article class="item-news"><h3 class="title-news">'
                            '<a href="/old-2222222.html">Old</a></h3></article>')
        if url == crawler.METADATA_URL:
            data = []
            for article_id in params["article_id"].split(","):
                age = timedelta(days={"1111111": 2, "2222222": 8, "3333333": 7}[article_id])
                data.append({
                    "article_id": article_id,
                    "title": "<b>Bài viết mới</b>" if article_id == "1111111" else "Quiet",
                    "publish_time": int((self.now - age).timestamp()),
                    "article_type": 1, "site_id": 1000000, "original_cate": 1000,
                })
            return Response(payload={"data": data})
        if url == crawler.COMMENTS_URL:
            if params["objectid"] == "3333333":
                return Response(payload={"data": {"items": [], "total": 0}})
            if params["offset"]:
                return Response(payload={"data": {"items": [], "total": 3}})
            return Response(payload={"data": {"total": 3, "items": [{
                "comment_id": 7, "userlike": 2, "content": "Parent",
                "replys": {"items": [
                    {"comment_id": 8, "userlike": 9, "content": "<b>Best reply</b>"},
                    {"comment_id": 9, "userlike": 1, "content": "Other reply"},
                ]},
            }]}})
        if url.endswith("recent-1111111.html"):
            return Response('<meta property="og:image" content="/photo.jpg">')
        if url.endswith("quiet-3333333.html"):
            return Response("<html><body>No image</body></html>")
        raise AssertionError(f"Unexpected request: {url} {params}")

    def test_seven_day_filter_fields_and_ranking(self):
        rows = crawler.crawl(self.fetch, self.now, max_pages=3, archives=("thoi-su",))
        self.assertEqual(len(rows), 2)
        row = rows[0]
        self.assertEqual(row["rank"], 1)
        self.assertEqual(row["article"], "Bài viết mới")
        self.assertEqual(row["image_url"], "https://vnexpress.net/photo.jpg")
        self.assertEqual(row["comment_count"], 3)
        self.assertEqual(row["observed_reactions"], 12)
        self.assertEqual(row["engagement_score"], 15)
        self.assertEqual(row["comment_id"], "8")
        self.assertEqual(row["text"], "Best reply")
        self.assertEqual(row["likes"], 9)
        self.assertEqual(rows[1]["rank"], 2)
        self.assertEqual(rows[1]["comment_count"], 0)
        self.assertEqual(rows[1]["text"], "")
        self.assertEqual(rows[1]["image_url"], "")
        self.assertTrue(all("old-2222222" not in call[0] for call in self.calls))

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "articles.csv"
            crawler.write_csv(rows, output)
            with output.open(encoding="utf-8-sig", newline="") as stream:
                saved = list(csv.DictReader(stream))
            self.assertEqual(saved[0]["engagement_score"], "15")

    def test_page_limit_refuses_incomplete_window(self):
        with self.assertRaisesRegex(RuntimeError, "increase --max-pages"):
            crawler.crawl(self.fetch, self.now, max_pages=1, archives=("thoi-su",))

    def test_repeated_listing_refuses_incomplete_window(self):
        def exhausted_listing(url, params=None):
            if url.endswith("/thoi-su-p2"):
                return self.fetch(crawler.BASE_URL + "/thoi-su")
            return self.fetch(url, params)

        with self.assertRaisesRegex(RuntimeError, "cannot claim a complete crawl"):
            crawler.crawl(exhausted_listing, self.now, max_pages=3, archives=("thoi-su",))

    def test_articles_shared_by_archives_are_crawled_once(self):
        def shared_archive(url, params=None):
            return self.fetch(url.replace("the-gioi", "thoi-su"), params)

        rows = crawler.crawl(
            shared_archive, self.now, max_pages=3,
            archives=("thoi-su", "the-gioi"),
        )
        self.assertEqual(len(rows), 2)
        comment_requests = [
            request for request in self.calls if request[0] == crawler.COMMENTS_URL
        ]
        self.assertEqual(len(comment_requests), 3)

    def test_retries_remote_disconnect(self):
        class FlakySession:
            calls = 0

            def get(self, url, params=None, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise requests.ConnectionError("Remote end closed connection")
                return Response("recovered")

        session = FlakySession()
        result = crawler.make_fetch(session, delay=0, retries=1, backoff=0)(
            "https://vnexpress.net/example.html"
        )
        self.assertEqual(result.text, "recovered")
        self.assertEqual(session.calls, 2)

    def test_resume_skips_completed_article_and_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "articles_7d.csv"
            disconnected = False

            def first_run(url, params=None):
                nonlocal disconnected
                if url.endswith("quiet-3333333.html") and not disconnected:
                    disconnected = True
                    raise RuntimeError("Remote end closed connection")
                return self.fetch(url, params)

            with self.assertRaisesRegex(RuntimeError, "rerun the same command to resume"):
                crawler.crawl_resumable(
                    first_run, self.now, 3, output, archives=("thoi-su",)
                )
            self.assertTrue(crawler.checkpoint_path(output).exists())
            with crawler.partial_path(output).open(encoding="utf-8-sig", newline="") as stream:
                partial = list(csv.DictReader(stream))
            self.assertEqual([row["article_id"] for row in partial], ["1111111"])
            status = crawler.checkpoint_status(output)
            self.assertEqual((status["completed"], status["total"]), (1, 2))
            self.assertEqual(status["next_article_id"], "3333333")
            self.assertTrue(status["next_url"].endswith("quiet-3333333.html"))

            self.calls.clear()
            rows, failures = crawler.crawl_resumable(
                self.fetch, self.now + timedelta(days=1), 3, output,
                archives=("thoi-su",),
            )
            self.assertEqual(failures, [])
            self.assertEqual(len(rows), 2)
            self.assertFalse(any("/thoi-su" in url for url, _ in self.calls))
            self.assertFalse(any(
                url == crawler.COMMENTS_URL and params["objectid"] == "1111111"
                for url, params in self.calls
            ))

    def test_keep_going_leaves_failed_article_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "articles_7d.csv"

            def failing_article(url, params=None):
                if url.endswith("recent-1111111.html"):
                    raise RuntimeError("Remote end closed connection")
                return self.fetch(url, params)

            rows, failures = crawler.crawl_resumable(
                failing_article, self.now, 3, output, keep_going=True,
                archives=("thoi-su",),
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["article_id"], "3333333")
            self.assertEqual(len(failures), 1)
            self.assertTrue(crawler.partial_path(output).exists())

            self.calls.clear()
            rows, failures = crawler.crawl_resumable(
                self.fetch, self.now, 3, output, archives=("thoi-su",)
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(failures, [])
            self.assertFalse(any("quiet-3333333.html" in url for url, _ in self.calls))


if __name__ == "__main__":
    unittest.main()
