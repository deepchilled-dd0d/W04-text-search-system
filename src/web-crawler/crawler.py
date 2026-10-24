"""Crawl and rank VnExpress articles published during the last seven days."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://vnexpress.net"
METADATA_URL = "https://gw.vnexpress.net/ar/get_basic"
COMMENTS_URL = "https://usi-saas.vnexpress.net/index/get"
WINDOW = timedelta(days=7)
ARCHIVES = (
    "thoi-su", "the-gioi", "kinh-doanh", "phap-luat", "khoa-hoc-cong-nghe",
    "the-thao", "giai-tri", "suc-khoe", "doi-song", "tam-su", "giao-duc",
    "oto-xe-may", "bat-dong-san", "goc-nhin", "du-lich", "y-kien",
)
ARTICLE_PATTERN = re.compile(r"-(\d{6,})\.html$")
OUTPUT = Path(__file__).resolve().parent.parent / "results" / "articles_7d.csv"
FIELDS = [
    "rank", "article_id", "article", "url", "published_at", "image_url",
    "comment_count", "comment_count_source", "observed_reactions",
    "engagement_score", "comment_id", "text", "likes",
]


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; CS419TextSearch/1.0)",
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
        "Referer": BASE_URL + "/",
    })
    return session


def make_fetch(session: requests.Session, delay: float, retries: int = 4, backoff: float = 1.0):
    def fetch(url: str, params: dict | None = None):
        for attempt in range(retries + 1):
            if delay:
                time.sleep(delay)
            response = None
            try:
                response = session.get(url, params=params, timeout=20)
                response.raise_for_status()
                return response
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as exc:
                retryable = True
            except requests.HTTPError as exc:
                retryable = response is not None and response.status_code in {
                    408, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524,
                }
            except requests.RequestException as exc:
                retryable = False
            if not retryable or attempt == retries:
                raise RuntimeError(
                    f"Cannot fetch {url} after {attempt + 1} attempt(s): {exc}"
                ) from exc
            wait = min(backoff * 2 ** attempt, 30)
            print(f"Retrying {url} in {wait:g}s ({attempt + 1}/{retries})", file=sys.stderr)
            if wait:
                time.sleep(wait)
        raise AssertionError("retry loop did not return or raise")

    return fetch


def article_links(html: str) -> dict[str, str]:
    links = {}
    soup = BeautifulSoup(html, "html.parser")
    story_links = soup.select("article.item-news h3.title-news a[href]")
    for link in story_links or soup.select("h3.title-news a[href]") or soup.select("a[href]"):
        url = urljoin(BASE_URL, link["href"])
        parsed = urlparse(url)
        if parsed.hostname not in {"vnexpress.net", "www.vnexpress.net"}:
            continue
        match = ARTICLE_PATTERN.search(parsed.path)
        if match:
            links.setdefault(match.group(1), parsed._replace(query="", fragment="").geturl())
    return links


def get_metadata(fetch, ids: list[str]) -> list[dict]:
    articles = []
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        payload = fetch(METADATA_URL, {
            "article_id": ",".join(batch),
            "data_select": "article_id,title,publish_time,article_type,original_cate,site_id",
        }).json()
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("items", list(data.values()))
        if not isinstance(data, list):
            raise ValueError("Unexpected article metadata response")
        articles.extend(item for item in data if isinstance(item, dict))
    return articles


def recent_articles(fetch, now: datetime, max_pages: int, archives=ARCHIVES) -> list[dict]:
    cutoff = (now - WINDOW).timestamp()
    current = now.timestamp()
    recent = {}

    for archive in archives:
        seen_in_archive = set()
        reached_cutoff = False
        for page in range(1, max_pages + 1):
            path = f"/{archive}" + (f"-p{page}" if page > 1 else "")
            links = article_links(fetch(BASE_URL + path).text)
            new_ids = [article_id for article_id in links if article_id not in seen_in_archive]
            if not new_ids:
                raise RuntimeError(
                    f"Archive {archive} ended before reaching the seven-day cutoff; "
                    "cannot claim a complete crawl."
                )
            seen_in_archive.update(new_ids)
            metadata = get_metadata(fetch, new_ids)
            published_by_id = {}
            for article in metadata:
                article_id = str(article.get("article_id", ""))
                if article_id not in links:
                    continue
                try:
                    published = int(article["publish_time"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Missing publication time for {article_id}") from exc
                published_by_id[article_id] = published
                if cutoff <= published <= current and article_id not in recent:
                    article["url"] = links[article_id]
                    article["archive"] = archive
                    recent[article_id] = article

            if not published_by_id:
                raise RuntimeError(f"No publication times returned for {archive} page {page}")
            last_id = next((article_id for article_id in reversed(new_ids) if article_id in published_by_id), None)
            print(f"{archive} page {page}: {len(new_ids)} links; {len(recent)} unique recent articles")
            if last_id and published_by_id[last_id] < cutoff:
                reached_cutoff = True
                break
        if not reached_cutoff:
            raise RuntimeError(
                f"Archive {archive} still has recent articles at page {max_pages}; "
                "increase --max-pages."
            )
    return list(recent.values())


def image_url(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector, attribute in [
        ('meta[property="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ("article img", "data-src"),
        ("article img", "src"),
    ]:
        node = soup.select_one(selector)
        if node and node.get(attribute):
            return urljoin(page_url, node[attribute])
    return ""


def nonnegative_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def comments_for_article(fetch, article: dict) -> dict:
    article_id = str(article["article_id"])
    comments = {}
    parent_ids = set()
    offset = 0
    reported_total = None

    while True:
        payload = fetch(COMMENTS_URL, {
            "objectid": article_id,
            "objecttype": article["article_type"],
            "siteid": article["site_id"],
            "categoryid": article["original_cate"],
            "sort": "like",
            "tab_active": "most_like",
            "offset": offset,
            "limit": 100,
        }).json()
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected comments response for article {article_id}")
        if reported_total is None:
            for value in (data.get("total_comment"), data.get("total"), payload.get("total")):
                reported_total = nonnegative_int(value)
                if reported_total is not None:
                    break
        items = data.get("items") or []
        if not isinstance(items, list):
            raise ValueError(f"Unexpected comment list for article {article_id}")
        if not items:
            break

        new_parents = 0
        for parent in items:
            if not isinstance(parent, dict):
                continue
            parent_id = str(parent.get("comment_id", ""))
            if parent_id and parent_id not in parent_ids:
                parent_ids.add(parent_id)
                new_parents += 1
            replies = parent.get("replys") or {}
            nested = (replies.get("items") or []) if isinstance(replies, dict) else []
            for comment in [parent, *nested]:
                if not isinstance(comment, dict):
                    continue
                comment_id = str(comment.get("comment_id", ""))
                if comment_id:
                    comments[comment_id] = comment
        if not new_parents:
            raise ValueError(f"Repeated comment page for article {article_id}")
        offset += len(items)

    observed_count = len(comments)
    comment_count = max(reported_total or 0, observed_count)
    reactions = sum(nonnegative_int(comment.get("userlike")) or 0 for comment in comments.values())
    top = sorted(
        comments.values(),
        key=lambda comment: (
            -(nonnegative_int(comment.get("userlike")) or 0),
            str(comment.get("comment_id", "")),
        ),
    )[:1]
    winner = top[0] if top else {}
    return {
        "comment_count": comment_count,
        "comment_count_source": "api" if reported_total is not None and reported_total >= observed_count else "observed",
        "observed_reactions": reactions,
        "engagement_score": comment_count + reactions,
        "comment_id": str(winner.get("comment_id") or f"article:{article_id}"),
        "text": BeautifulSoup(winner.get("content") or "", "html.parser").get_text(" ", strip=True),
        "likes": nonnegative_int(winner.get("userlike")) or 0,
    }


def article_row(fetch, article: dict) -> dict:
    article_id = str(article["article_id"])
    url = article["url"]
    details = comments_for_article(fetch, article)
    row = {
        "article_id": article_id,
        "article": BeautifulSoup(article.get("title") or "", "html.parser").get_text(" ", strip=True),
        "url": url,
        "published_at": datetime.fromtimestamp(int(article["publish_time"]), timezone.utc).isoformat(),
        "image_url": image_url(fetch(url).text, url),
        **details,
    }
    print(f"{article_id}: {details['comment_count']} comments, {details['observed_reactions']} observed reactions")
    return row


def rank_rows(rows: list[dict]) -> list[dict]:
    rows.sort(key=lambda row: (-row["engagement_score"], -row["comment_count"], row["article_id"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def crawl(fetch, now: datetime, max_pages: int, archives=ARCHIVES) -> list[dict]:
    return rank_rows([
        article_row(fetch, article)
        for article in recent_articles(fetch, now, max_pages, archives)
    ])


def checkpoint_path(output: Path) -> Path:
    return output.with_name(output.stem + ".checkpoint.sqlite")


def partial_path(output: Path) -> Path:
    return output.with_name(output.stem + ".partial.csv")


def checkpoint_status(output: Path) -> dict:
    checkpoint = checkpoint_path(output)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"No checkpoint at {checkpoint}")
    database = sqlite3.connect(checkpoint)
    try:
        started = database.execute(
            "SELECT value FROM state WHERE key = 'started_at'"
        ).fetchone()
        total = database.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        completed = database.execute("SELECT COUNT(*) FROM completed").fetchone()[0]
        next_article = database.execute(
            "SELECT articles.article_id, articles.data "
            "FROM articles LEFT JOIN completed USING (article_id) "
            "WHERE completed.article_id IS NULL ORDER BY articles.position LIMIT 1"
        ).fetchone()
        next_url = json.loads(next_article[1]).get("url") if next_article else None
    except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid checkpoint {checkpoint}; use --restart") from exc
    finally:
        database.close()
    if not started:
        raise ValueError(f"Invalid checkpoint {checkpoint}; use --restart")
    return {
        "started_at": started[0],
        "total": total,
        "completed": completed,
        "next_article_id": next_article[0] if next_article else None,
        "next_url": next_url,
    }


def crawl_resumable(fetch, now: datetime, max_pages: int, output: Path,
                    keep_going: bool = False, archives=ARCHIVES) -> tuple[list[dict], list[str]]:
    """Commit each completed article so a later invocation skips it."""
    checkpoint = checkpoint_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if checkpoint.exists():
        database = sqlite3.connect(checkpoint)
        try:
            started = database.execute("SELECT value FROM state WHERE key = 'started_at'").fetchone()
            if not started:
                raise ValueError(f"Invalid checkpoint {checkpoint}; use --restart")
            articles = [json.loads(value) for (value,) in database.execute(
                "SELECT data FROM articles ORDER BY position"
            )]
            print(f"Resuming crawl started {started[0]}: {len(articles)} articles discovered")
        except (sqlite3.DatabaseError, json.JSONDecodeError, ValueError):
            database.close()
            raise ValueError(f"Invalid checkpoint {checkpoint}; use --restart")
    else:
        articles = recent_articles(fetch, now, max_pages, archives)
        database = sqlite3.connect(checkpoint)
        try:
            with database:
                database.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                database.execute(
                    "CREATE TABLE articles (article_id TEXT PRIMARY KEY, position INTEGER NOT NULL, data TEXT NOT NULL)"
                )
                database.execute(
                    "CREATE TABLE completed (article_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
                database.execute(
                    "INSERT INTO state VALUES (?, ?)", ("started_at", now.isoformat())
                )
                for position, article in enumerate(articles):
                    database.execute(
                        "INSERT INTO articles VALUES (?, ?, ?)",
                        (str(article["article_id"]), position, json.dumps(article, ensure_ascii=False)),
                    )
            print(f"Saved discovery checkpoint: {len(articles)} articles")
        except Exception:
            database.close()
            checkpoint.unlink(missing_ok=True)
            raise

    try:
        done = {
            article_id for (article_id,) in database.execute("SELECT article_id FROM completed")
        }
        print(f"Checkpoint progress: {len(done)}/{len(articles)} articles completed")
        failures = []
        for article in articles:
            article_id = str(article["article_id"])
            if article_id in done:
                continue
            try:
                row = article_row(fetch, article)
            except (RuntimeError, ValueError, KeyError) as exc:
                message = f"Article {article_id} ({article['url']}): {exc}"
                failures.append(message)
                if not keep_going:
                    rows = rank_rows([
                        json.loads(value) for (value,) in database.execute("SELECT data FROM completed")
                    ])
                    write_csv(rows, partial_path(output))
                    raise RuntimeError(
                        f"{message}. Saved {len(rows)} completed articles to "
                        f"{partial_path(output)}; rerun the same command to resume."
                    ) from exc
                print(f"Skipping for this run: {message}", file=sys.stderr)
                continue
            with database:
                database.execute(
                    "INSERT OR REPLACE INTO completed VALUES (?, ?)",
                    (article_id, json.dumps(row, ensure_ascii=False)),
                )
            done.add(article_id)

        rows = rank_rows([
            json.loads(value) for (value,) in database.execute("SELECT data FROM completed")
        ])
        if failures:
            write_csv(rows, partial_path(output))
        return rows, failures
    finally:
        database.close()


def write_csv(rows: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8-sig", newline="", dir=output.parent,
            prefix=output.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    parser.add_argument("--retries", type=int, default=4, help="retries for transient request failures")
    parser.add_argument("--keep-going", action="store_true", help="save completed rows and continue past failed articles")
    parser.add_argument("--restart", action="store_true", help="discard the checkpoint and start a fresh seven-day window")
    parser.add_argument("--status", action="store_true", help="show saved progress without making network requests")
    args = parser.parse_args(argv)
    if args.max_pages < 1 or args.delay < 0 or args.retries < 0:
        parser.error("--max-pages must be positive; --delay and --retries cannot be negative")
    if args.status:
        if args.restart:
            parser.error("--status and --restart cannot be combined")
        try:
            status = checkpoint_status(args.output)
        except (OSError, ValueError) as exc:
            parser.exit(1, f"Error: {exc}\n")
        print(f"Crawl started: {status['started_at']}")
        print(f"Completed: {status['completed']}/{status['total']} articles")
        if status["next_article_id"]:
            print(f"Next: {status['next_article_id']} {status['next_url']}")
        else:
            print("All articles are parsed; rerun to write the final CSV.")
        return 0
    if args.restart:
        checkpoint_path(args.output).unlink(missing_ok=True)
        partial_path(args.output).unlink(missing_ok=True)
    now = datetime.now(timezone.utc)
    with make_session() as session:
        try:
            rows, failures = crawl_resumable(
                make_fetch(session, args.delay, args.retries), now, args.max_pages,
                args.output, args.keep_going,
            )
        except (RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            parser.exit(1, f"Error: {exc}\n")
        except KeyboardInterrupt:
            parser.exit(130, f"Interrupted. Rerun the same command to resume from {checkpoint_path(args.output)}\n")
    if failures:
        parser.exit(
            1,
            f"{len(failures)} article(s) still failed. Saved {len(rows)} completed rows to "
            f"{partial_path(args.output)}. Rerun the same command to retry unfinished articles.\n",
        )
    write_csv(rows, args.output)
    checkpoint_path(args.output).unlink(missing_ok=True)
    partial_path(args.output).unlink(missing_ok=True)
    print(f"Saved {len(rows)} articles to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
