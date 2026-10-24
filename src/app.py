"""Streamlit interface for the VnExpress Whoosh search engine.

Run with: streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "text-search"))
import search as engine  # noqa: E402  (the CLI module lives in text-search/)


st.set_page_config(page_title="VnExpress search", page_icon="🔎", layout="centered")
st.title("VnExpress article search")
st.caption("Search article titles and the highest-liked comments with Whoosh.")

csv_path = engine.DEFAULT_CSV
if not csv_path.is_file():
    st.error(f"Dataset not found: {csv_path}. Run the crawler or restore the CSV first.")
    st.stop()

st.caption(f"Dataset: {csv_path.relative_to(ROOT)}")

with st.form("search_form"):
    query = st.text_input(
        "Search query",
        placeholder="For example: du hoc visa",
        help="Searches article titles and top comments. Vietnamese accents are optional.",
    )
    limit = st.selectbox("Maximum results", [10, 20, 50], index=0)
    submitted = st.form_submit_button("Search", type="primary")

if submitted:
    if not query.strip():
        st.session_state.pop("search_result", None)
        st.warning("Enter a search query.")
    else:
        try:
            # ensure_index builds the index on first use and only rebuilds when
            # the source CSV changes. Streamlit reruns therefore reuse it.
            with st.spinner("Searching articles..."):
                st.session_state["search_result"] = engine.search(
                    csv_path, engine.DEFAULT_INDEX, query, limit
                )
        except (OSError, ValueError) as exc:
            st.session_state.pop("search_result", None)
            st.error(f"Search failed: {exc}")

result = st.session_state.get("search_result")
if result is not None:
    st.subheader(f"{result['total']} matches")
    st.caption(f"Query: {result['query']}")
    if not result["results"]:
        st.info("No articles matched. Try fewer words or a different phrase.")

    for hit in result["results"]:
        with st.container(border=True):
            st.markdown(f"**{hit['article']}**")
            if hit["published_at"]:
                st.caption(f"Published: {hit['published_at']}")
            if hit["text"]:
                st.write(f"Top comment: {hit['text']}")
                st.caption(f"{hit['likes']} likes")
            else:
                st.caption("No comment was available for this article.")
            if hit["comment_count"] or hit["rank"]:
                st.caption(
                    f"{hit['comment_count']} comments · "
                    f"{hit['observed_reactions']} observed reactions · "
                    f"Engagement rank {hit['rank']}"
                )
            st.link_button("Read article", hit["url"])
