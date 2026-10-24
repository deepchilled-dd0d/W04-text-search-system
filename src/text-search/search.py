"""Search the Week 1 VnExpress comment export with Whoosh."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

try:
    from whoosh import index
    from whoosh.analysis import CharsetFilter, LowercaseFilter, RegexTokenizer
    from whoosh.fields import ID, NUMERIC, TEXT, Schema
    from whoosh.qparser import AndGroup, MultifieldParser
except ImportError as exc:
    raise SystemExit(
        "Whoosh is required. Install it with: python -m pip install -r requirements.txt"
    ) from exc


ROOT = Path(__file__).resolve().parent.parent
NEW_CSV = ROOT / "results" / "articles_7d.csv"
DEFAULT_CSV = NEW_CSV if NEW_CSV.is_file() else ROOT / "results" / "top_comments.csv"
DEFAULT_INDEX = ROOT / "text-search" / "index"
REQUIRED_COLUMNS = {"article", "url", "comment_id", "text", "likes"}
INDEX_VERSION = 2


def vietnamese_charmap() -> dict[int, str]:
    """Map Latin letters, including Vietnamese tone marks, to plain letters."""
    mapping = {ord("đ"): "d"}
    for codepoint in range(0x00C0, 0x2000):
        character = chr(codepoint)
        folded = "".join(
            part for part in unicodedata.normalize("NFKD", character)
            if unicodedata.category(part) != "Mn"
        ).lower()
        if len(folded) == 1 and "a" <= folded <= "z" and folded != character:
            mapping[codepoint] = folded
    return mapping


def schema() -> Schema:
    analyzer = RegexTokenizer() | LowercaseFilter() | CharsetFilter(vietnamese_charmap())
    return Schema(
        comment_id=ID(stored=True, unique=True),
        article=TEXT(stored=True, analyzer=analyzer),
        text=TEXT(stored=True, analyzer=analyzer),
        url=ID(stored=True),
        likes=NUMERIC(stored=True, signed=False),
        image_url=ID(stored=True),
        published_at=ID(stored=True),
        comment_count=NUMERIC(stored=True, signed=False),
        observed_reactions=NUMERIC(stored=True, signed=False),
        engagement_score=NUMERIC(stored=True, signed=False),
        rank=NUMERIC(stored=True, signed=False),
    )


def csv_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_comments(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(sorted(missing))}")
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(row[name] is None for name in REQUIRED_COLUMNS):
                raise ValueError(f"Malformed CSV row {row_number}")
            try:
                likes = int(row["likes"])
            except ValueError as exc:
                raise ValueError(f"Invalid likes value on row {row_number}") from exc
            if likes < 0 or not row["comment_id"].strip():
                raise ValueError(f"Invalid comment ID or likes on row {row_number}")
            yield {
                "comment_id": row["comment_id"].strip(),
                "article": row["article"].strip(),
                "text": row["text"].strip(),
                "url": row["url"].strip(),
                "likes": likes,
                "image_url": (row.get("image_url") or "").strip(),
                "published_at": (row.get("published_at") or "").strip(),
                "comment_count": int(row.get("comment_count") or 0),
                "observed_reactions": int(row.get("observed_reactions") or 0),
                "engagement_score": int(row.get("engagement_score") or 0),
                "rank": int(row.get("rank") or 0),
            }


def build_index(csv_path: Path, index_dir: Path) -> int:
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    comments = list(read_comments(csv_path))
    ids = [comment["comment_id"] for comment in comments]
    if len(ids) != len(set(ids)):
        raise ValueError("CSV contains duplicate comment IDs")
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "source.json").unlink(missing_ok=True)
    search_index = index.create_in(index_dir, schema())
    writer = search_index.writer()
    try:
        for comment in comments:
            writer.add_document(**comment)
        writer.commit()
    except Exception:
        writer.cancel()
        raise
    metadata = {
        "version": INDEX_VERSION,
        "source": str(csv_path.resolve()),
        "sha256": csv_digest(csv_path),
    }
    (index_dir / "source.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(comments)


def ensure_index(csv_path: Path, index_dir: Path) -> None:
    metadata_path = index_dir / "source.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        metadata = {}
    if (
        not index.exists_in(index_dir)
        or metadata.get("version") != INDEX_VERSION
        or metadata.get("source") != str(csv_path.resolve())
        or metadata.get("sha256") != csv_digest(csv_path)
    ):
        count = build_index(csv_path, index_dir)
        print(f"Indexed {count} comments from {csv_path}", file=sys.stderr)


def search(csv_path: Path, index_dir: Path, query: str, limit: int) -> dict:
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    if not query.strip():
        raise ValueError("Search query cannot be empty")
    if limit < 1:
        raise ValueError("Limit must be at least 1")
    ensure_index(csv_path, index_dir)
    search_index = index.open_dir(index_dir)
    parser = MultifieldParser(
        ["article", "text"], search_index.schema,
        fieldboosts={"article": 2.0, "text": 1.0}, group=AndGroup,
    )
    parsed = parser.parse(unicodedata.normalize("NFC", query))
    with search_index.searcher() as searcher:
        results = searcher.search(parsed, limit=limit)
        return {
            "query": query,
            "total": len(results),
            "results": [
                {
                    "article": hit["article"],
                    "url": hit["url"],
                    "comment_id": hit["comment_id"],
                    "text": hit["text"],
                    "likes": hit["likes"],
                    "image_url": hit["image_url"],
                    "published_at": hit["published_at"],
                    "comment_count": hit["comment_count"],
                    "observed_reactions": hit["observed_reactions"],
                    "engagement_score": hit["engagement_score"],
                    "rank": hit["rank"],
                    "score": round(hit.score, 3),
                }
                for hit in results
            ],
        }


def display(result: dict) -> None:
    print(f"{result['total']} match(es) for {result['query']!r}")
    for number, hit in enumerate(result["results"], start=1):
        print(f"\n{number}. {hit['article']} ({hit['likes']} likes)")
        if hit["rank"]:
            print(
                f"   Engagement rank {hit['rank']}: {hit['comment_count']} comments + "
                f"{hit['observed_reactions']} observed reactions = {hit['engagement_score']}"
            )
        print(f"   {hit['text']}")
        print(f"   {hit['url']} [comment {hit['comment_id']}]")
        if hit["image_url"]:
            print(f"   Image: {hit['image_url']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="comment CSV")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX, help="Whoosh index directory")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("index", help="rebuild the search index")
    search_command = commands.add_parser("search", help="search article titles and comments")
    search_command.add_argument("query", help="words or a quoted phrase")
    search_command.add_argument("--limit", type=int, default=10)
    search_command.add_argument("--json", action="store_true", help="emit machine-readable results")
    shell_command = commands.add_parser("shell", help="search interactively")
    shell_command.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)

    try:
        if args.command == "index":
            print(f"Indexed {build_index(args.csv, args.index)} comments from {args.csv}")
        elif args.command == "search":
            result = search(args.csv, args.index, args.query, args.limit)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                display(result)
        else:
            ensure_index(args.csv, args.index)
            print("Enter a search query, or press Enter to quit.")
            while True:
                try:
                    query = input("search> ").strip()
                except EOFError:
                    break
                if not query:
                    break
                display(search(args.csv, args.index, query, args.limit))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
