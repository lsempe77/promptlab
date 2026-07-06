"""Backfill runs.co2e_grams from already-logged token counts (no new inference,
so it costs nothing / emits nothing). Idempotent: only fills rows where
co2e_grams IS NULL and a completion-token count exists.

Usage (from repo root): python -m backend.scripts.backfill_carbon
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import carbon, db  # noqa: E402


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, model_id, completion_tokens, latency_ms FROM runs "
            "WHERE co2e_grams IS NULL AND completion_tokens IS NOT NULL"
        ).fetchall()
        print(f"Backfilling CO2e for {len(rows)} runs (from logged tokens)...")
        n = 0
        for r in rows:
            g = carbon.estimate_co2e_grams(r["model_id"], r["completion_tokens"], r["latency_ms"])
            if g is not None:
                conn.execute("UPDATE runs SET co2e_grams = ? WHERE id = ?", (g, r["id"]))
                n += 1
        conn.commit()
        total = conn.execute("SELECT SUM(co2e_grams) AS s FROM runs").fetchone()["s"] or 0.0
        print(f"Updated {n} rows. Total logged footprint so far: {total:.1f} gCO2e "
              f"({total / 1000:.3f} kgCO2e).")


if __name__ == "__main__":
    main()
