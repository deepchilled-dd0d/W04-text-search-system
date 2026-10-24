# Result provenance

`top_comments.csv` is a copy of the repository's existing Week 1 `top_comments.csv` snapshot. It contains 77 comments from 46 articles. A fresh crawl was attempted on 23 October 2026, but this environment could not resolve `vnexpress.net`, so no new articles were fetched.

`live_articles_2026-10-23.csv` is a separate, manually verified snapshot of nine current VnExpress articles gathered through the available web reader on 23 October 2026. Article titles, URLs, and publication times were checked against the article pages. It does not include comment counts or top comments because the reader cannot access the comment API. It is not a full seven-day crawl.

The updated crawler uses paginated category archives and writes a separate `articles_7d.csv` only after it completes a seven-day run. This file has not been generated in this sandbox because outbound sockets are blocked.
