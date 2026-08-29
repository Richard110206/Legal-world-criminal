"""Unit tests for src.utils.file_io."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.utils.file_io import safe_read_json, timestamp_tag, utc_timestamp, write_json


class TestUtcTimestamp:
    def test_is_timezone_aware_utc(self) -> None:
        parsed = datetime.fromisoformat(utc_timestamp())
        assert parsed.tzinfo is not None, "timestamp must carry tzinfo"
        assert parsed.utcoffset().total_seconds() == 0

    def test_lexicographic_ordering(self) -> None:
        assert utc_timestamp() <= utc_timestamp()


class TestTimestampTag:
    def test_format(self) -> None:
        assert len(timestamp_tag()) == 15  # YYYYmmdd_HHMMSS
        assert timestamp_tag()[8] == "_"


class TestJsonIo:
    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "data.json"
        payload = {"name": "张三", "items": [1, 2, 3]}
        write_json(target, payload)
        assert json.loads(target.read_text(encoding="utf-8")) == payload

    def test_write_is_atomic_no_tmp_leftover(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        write_json(target, {"a": 1})
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_read_missing_returns_default(self, tmp_path: Path) -> None:
        assert safe_read_json(tmp_path / "nope.json", default={"x": 9}) == {"x": 9}

    def test_read_corrupt_returns_default(self, tmp_path: Path) -> None:
        target = tmp_path / "broken.json"
        target.write_text("{not json", encoding="utf-8")
        assert safe_read_json(target, default=None) is None
