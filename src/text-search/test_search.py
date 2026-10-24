"""Integration checks for the Whoosh index and Vietnamese search behavior."""

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import search as engine


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.csv_path = Path(self.temp.name) / "comments.csv"
        self.index_dir = Path(self.temp.name) / "index"
        self.write_rows([
            {
                "article": "Du học tại Australia",
                "url": "https://vnexpress.net/one.html",
                "comment_id": "1",
                "text": "Chuẩn bị hồ sơ visa thật kỹ.",
                "likes": "8",
            },
            {
                "article": "Điện thoại mới",
                "url": "https://vnexpress.net/two.html",
                "comment_id": "2",
                "text": "Màn hình rất đẹp.",
                "likes": "3",
            },
        ])

    def write_rows(self, rows):
        with self.csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=["article", "url", "comment_id", "text", "likes"]
            )
            writer.writeheader()
            writer.writerows(rows)

    def test_searches_titles_and_comments_without_accents(self):
        result = engine.search(self.csv_path, self.index_dir, "du hoc visa", 10)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["comment_id"], "1")
        self.assertEqual(result["results"][0]["likes"], 8)
        self.assertEqual(
            engine.search(self.csv_path, self.index_dir, "dien thoai", 10)["total"], 1
        )

    def test_rebuilds_after_csv_change(self):
        engine.search(self.csv_path, self.index_dir, "visa", 10)
        self.write_rows([{
            "article": "Bài mới", "url": "https://vnexpress.net/three.html",
            "comment_id": "3", "text": "Tin mới", "likes": "0",
        }])
        self.assertEqual(engine.search(self.csv_path, self.index_dir, "visa", 10)["total"], 0)
        self.assertEqual(engine.search(self.csv_path, self.index_dir, "tin", 10)["total"], 1)

    def test_reuses_index_for_repeated_searches(self):
        engine.search(self.csv_path, self.index_dir, "visa", 10)
        with patch.object(engine, "build_index", side_effect=AssertionError("rebuilt")):
            self.assertEqual(
                engine.search(self.csv_path, self.index_dir, "dien thoai", 10)["total"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
