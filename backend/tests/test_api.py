"""Tests for backend.app.api — the HTTP layer over the read-only SQLite DB.

The existing suite covers the pure functions (analytics, scoring, parsing...),
but nothing exercised the endpoints themselves. That is precisely where the
`/confusion` regression lived: `analytics.compute_confusion` was correct, while
api.py filtered on `prompt_version_id = NULL` (which matches no rows in SQL) and
so returned an all-zero matrix for every model on every field.

These tests build a small deterministic SQLite fixture and drive the real app
through TestClient.
"""
from __future__ import annotations

import importlib
import os
import tempfile
from pathlib import Path

import pytest

# The DB path is read from DEP_DB_PATH at import time, so it must be set before
# backend.app.db is (re)loaded — hence the env var + reload dance here.
_TMPDIR = tempfile.mkdtemp(prefix="promptlab_apitest_")
_DB_PATH = Path(_TMPDIR) / "test.db"
os.environ["DEP_DB_PATH"] = str(_DB_PATH)
os.environ.pop("DATABASE_URL", None)  # keep the Postgres branch out of these tests

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import db as _db  # noqa: E402

importlib.reload(_db)
from backend.app import api as _api  # noqa: E402

importlib.reload(_api)

PROJECT = "dep-extraction"
MODEL_A = "test/model-a"
MODEL_B = "test/model-b"


def _seed() -> None:
    """Deterministic fixture.

    authors (list_text):
      rec 1 truth [Smith, Doe]      pred [Smith, Doe]           -> tp 2
      rec 2 truth [Roe]             pred [Roe, Extra]           -> tp 1, fp 1
      => tp=3 fp=1 fn=0, precision 0.75, recall 1.0, n=2

    sector_name (single_categorical):
      rec 1 truth Health    pred Health     -> correct
      rec 2 truth Education pred Health     -> wrong      => accuracy 0.5
    """
    _db.init_db(_DB_PATH)
    with _db.get_conn(_DB_PATH) as conn:
        pid = _db.get_project_id(conn, PROJECT)

        _db.upsert_record(conn, pid, 1, "Paper One", "/tmp/1.md")
        _db.upsert_record(conn, pid, 2, "Paper Two", "/tmp/2.md")

        _db.upsert_ground_truth(conn, pid, 1, "authors", ["Smith, John", "Doe, Jane"])
        _db.upsert_ground_truth(conn, pid, 2, "authors", ["Roe, Ann"])
        _db.upsert_ground_truth(conn, pid, 1, "sector_name", "Health")
        _db.upsert_ground_truth(conn, pid, 2, "sector_name", "Education")

        pv_authors = _db.add_prompt_version(
            conn, pid, "authors", 1, "baseline authors instruction", None, "seed", 1, None
        )
        pv_sector = _db.add_prompt_version(
            conn, pid, "sector_name", 1, "baseline sector instruction", None, "seed", 1, None
        )

        for rec, pred in ((1, ["Smith, John", "Doe, Jane"]), (2, ["Roe, Ann", "Extra, Person"])):
            _db.add_run(
                conn, project_id=pid, field_name="authors", record_id=rec, model_id=MODEL_A,
                prompt_version_id=pv_authors, parsed_value=pred, score=1.0, is_correct=1,
            )
        for rec, pred, ok in ((1, "Health", 1), (2, "Health", 0)):
            _db.add_run(
                conn, project_id=pid, field_name="sector_name", record_id=rec, model_id=MODEL_A,
                prompt_version_id=pv_sector, parsed_value=pred, score=float(ok), is_correct=ok,
            )
        # A second model with a single run, to test model_id filtering.
        _db.add_run(
            conn, project_id=pid, field_name="authors", record_id=1, model_id=MODEL_B,
            prompt_version_id=pv_authors, parsed_value=["Smith, John"], score=0.5, is_correct=0,
        )


_seed()
client = TestClient(_api.app)


def _confusion(field: str, **params) -> dict:
    r = client.get(f"/api/projects/{PROJECT}/fields/{field}/confusion", params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestHealth:
    def test_health_ok(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestConfusionRegression:
    """The shipped bug: no prompt_version -> SQL `= NULL` -> zero rows."""

    def test_no_version_returns_data(self):
        # Regression: this used to return tp=fp=fn=0, n=0.
        # Unfiltered => aggregates BOTH models (2 runs from A + 1 from B).
        c = _confusion("authors")
        assert c["n"] == 3
        assert c["tp"] == 4
        assert c["fp"] == 1
        assert c["fn"] == 1

    def test_no_version_matches_explicit_version(self):
        assert _confusion("authors") == _confusion("authors", prompt_version=1)

    def test_unknown_version_is_empty(self):
        # pvid == -1 must still filter, yielding an empty (not full) result.
        c = _confusion("authors", prompt_version=999)
        assert c["n"] == 0
        assert c["tp"] == 0

    def test_model_filter(self):
        c = _confusion("authors", model_id=MODEL_B)
        assert c["n"] == 1
        assert c["tp"] == 1
        assert c["fn"] == 1  # model B missed "Doe, Jane"

    def test_list_field_metrics(self):
        c = _confusion("authors", model_id=MODEL_A)
        assert c["type"] == "list"
        assert c["n"] == 2
        assert (c["tp"], c["fp"], c["fn"]) == (3, 1, 0)
        assert c["precision"] == pytest.approx(0.75)
        assert c["recall"] == pytest.approx(1.0)

    def test_categorical_field_metrics(self):
        c = _confusion("sector_name")
        assert c["type"] == "categorical"
        assert c["n"] == 2
        assert c["accuracy"] == pytest.approx(0.5)


class TestModelsSummary:
    def test_returns_all_models(self):
        r = client.get(f"/api/projects/{PROJECT}/fields/authors/models-summary")
        assert r.status_code == 200
        rows = r.json()
        assert {row["model_id"] for row in rows} == {MODEL_A, MODEL_B}

    def test_accuracy_computed(self):
        r = client.get(f"/api/projects/{PROJECT}/fields/sector_name/models-summary")
        row = next(x for x in r.json() if x["model_id"] == MODEL_A)
        assert row["n"] == 2
        assert row["accuracy"] == pytest.approx(0.5)


class TestNotFound:
    def test_unknown_project_404(self):
        r = client.get("/api/projects/nope/fields/authors/confusion")
        assert r.status_code == 404

    def test_unknown_field_404(self):
        r = client.get(f"/api/projects/{PROJECT}/fields/not_a_field/confusion")
        assert r.status_code == 404
