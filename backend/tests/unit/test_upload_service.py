"""upload_service 单测（WP2 从 upload.py 提取）。"""
from __future__ import annotations

import hashlib
from pathlib import Path

from app.services.upload_service import (
    IngestResult,
    compute_md5,
    is_image_file,
    safe_rel_path,
)


def test_compute_md5_sha256():
    assert compute_md5(b"hello") == hashlib.sha256(b"hello").hexdigest()
    assert compute_md5(b"") == hashlib.sha256(b"").hexdigest()


def test_is_image_file():
    assert is_image_file("a.jpg") is True
    assert is_image_file("a.PNG") is True
    assert is_image_file("a.pdf") is False


def test_safe_rel_path_rejects_traversal():
    assert safe_rel_path("../etc/passwd") == Path("etc/passwd")


def test_safe_rel_path_strips_absolute():
    assert safe_rel_path("/etc/passwd") == Path("passwd")


def test_safe_rel_path_empty_falls_back_to_unnamed():
    assert safe_rel_path("") == Path("unnamed")


def test_ingest_result_defaults():
    r = IngestResult(
        file_name="a.txt",
        file_size=10,
        chunks_count=0,
        ingested_count=0,
        status="success",
    )
    assert r.error == ""
    assert r.entry_ids == []
    assert r.category is None
    assert r.series == ""
