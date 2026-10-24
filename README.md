# VnExpress text search

The crawler collects VnExpress articles published in the **last seven days**. It saves article title, URL, publication time, image URL, comment count, highest-liked observed comment, and a ranking by comments plus observed reactions. The search tool indexes the article title and top comment, ignores Vietnamese diacritics, and ranks query results by Whoosh relevance.

## Set up

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

To collect a fresh CSV when VnExpress is reachable, run:

```sh
.venv/bin/python web-crawler/crawler.py
```

The crawler writes `results/articles_7d.csv`. It paginates 16 VnExpress category archives independently, then deduplicates articles by ID. This avoids the short `tin-tuc-24h` listing. Each archive is followed until its chronological list reaches articles older than seven days. If `--max-pages` is reached or an archive ends before then, the crawler exits without replacing the previous CSV; `--max-pages` is a per-category limit.

### Resume after a disconnect

The crawler retries transient connection failures up to four times with increasing waits. After discovery, it saves a SQLite checkpoint and commits each completed article. If a request still fails, rerun with the **same output path** to resume at the unfinished article; completed articles are skipped. The crawl keeps its original seven-day time window during a resume. Completed rows are also written to `results/articles_7d.partial.csv` after a failure.

```sh
.venv/bin/python web-crawler/crawler.py
```

If disconnects continue, rerun with a longer request delay and more retries:

```sh
.venv/bin/python web-crawler/crawler.py --delay 2 --retries 6
```

Check the saved count and next article without accessing the network:

```sh
.venv/bin/python web-crawler/crawler.py --status
```

Use `--keep-going` to process other articles when one repeatedly fails. The failed article remains pending and will be retried on the next run. Use `--restart` only when you want to discard the checkpoint and start a new seven-day window. The run that failed before this checkpoint feature was added cannot be resumed at its exact position; its first rerun must repeat discovery.

The CSV has one row per article, including articles with no comments. `engagement_score` is `comment_count + observed_reactions`; `rank` sorts by this score in descending order. `comment_count_source` says whether the count came from the comments API or from comments returned by it. `observed_reactions` is the sum of likes on returned comments and replies, so it may be lower than the site's total when replies are not fully exposed by the API. `text` and `likes` contain the highest-liked returned comment. `image_url` is empty if the article has no discoverable image.

## Search

Start the graphical search interface:

```sh
.venv/bin/streamlit run app.py
```

Enter a query and click **Search** to see matching article titles, top comments, and links. The first search builds the Whoosh index; later searches and Streamlit reruns reuse the saved index. If the CSV changes, the index is rebuilt once for the new data.

The command-line interface is also available:

```sh
.venv/bin/python text-search/search.py search "du hoc"
.venv/bin/python text-search/search.py search '"Trung Quốc"' --limit 5
.venv/bin/python text-search/search.py search "visa" --json
.venv/bin/python text-search/search.py shell
```

The first search builds the index automatically. It uses `results/articles_7d.csv` when present, otherwise the older `results/top_comments.csv` snapshot, and rebuilds when its CSV changes. Run `index` to rebuild it explicitly:

```sh
.venv/bin/python text-search/search.py index
```

Pass `--csv path/to/file.csv` and `--index path/to/index` before the command to use other paths. The search tool also accepts the older five-column comment CSV.

## Test

```sh
.venv/bin/python -m unittest discover -s text-search -p 'test_*.py'
.venv/bin/python -m unittest discover -s web-crawler -p 'test_*.py'
```
