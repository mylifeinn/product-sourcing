from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, List
from datetime import datetime

from sourcing.models import ProductCandidate, CandidateDB
from sourcing.config import get_config


DB_PATH = Path(__file__).parent.parent.parent / "data" / "candidates.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    niche TEXT NOT NULL,
    pain_point_keywords TEXT NOT NULL,
    trend_score REAL DEFAULT 0,
    longtail_keywords TEXT NOT NULL,
    google_trends_yoy_pct REAL DEFAULT 0,
    tiktok_hashtag_growth_pct REAL DEFAULT 0,
    wholesale_price_usd REAL DEFAULT 0,
    estimated_retail_price_usd REAL DEFAULT 0,
    estimated_shipping_usd REAL DEFAULT 0,
    estimated_margin_pct REAL DEFAULT 0,
    weight_g REAL DEFAULT 0,
    dimensions_cm TEXT NOT NULL,
    shipping_channel TEXT DEFAULT '',
    supplier_rating REAL DEFAULT 0,
    refund_rate_pct REAL DEFAULT 0,
    has_actual_photos INTEGER DEFAULT 0,
    supplier_url TEXT DEFAULT '',
    supplier_contact TEXT DEFAULT '',
    uniqueness_passed INTEGER DEFAULT 0,
    competitor_urls TEXT NOT NULL,
    is_evergreen INTEGER DEFAULT 1,
    seasonal_peak_window_days INTEGER DEFAULT 0,
    prep_lead_time_days INTEGER DEFAULT 0,
    competitor_sales_90d INTEGER DEFAULT 0,
    competitor_reviews INTEGER DEFAULT 0,
    market_proof_urls TEXT NOT NULL,
    amazon_rating REAL DEFAULT 0,
    estimated_aov_usd REAL DEFAULT 0,
    estimated_repurchase_cycle_days INTEGER DEFAULT 0,
    estimated_ltv_orders REAL DEFAULT 0,
    patent_risk_level TEXT DEFAULT 'none',
    trademark_risk_level TEXT DEFAULT 'none',
    matched_patents TEXT NOT NULL,
    gate_results TEXT NOT NULL,
    gate_details TEXT NOT NULL,
    total_score INTEGER DEFAULT 0,
    passed_all_gates INTEGER DEFAULT 0,
    needs_manual_review INTEGER DEFAULT 0,
    data_completeness_pct REAL DEFAULT 0,
    data_provenance TEXT NOT NULL,
    review_status TEXT DEFAULT 'pending',
    review_notes TEXT DEFAULT '',
    shopify_draft_id INTEGER,
    shopify_product_id INTEGER,
    published_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_niche ON candidates(niche);
CREATE INDEX IF NOT EXISTS idx_review_status ON candidates(review_status);
CREATE INDEX IF NOT EXISTS idx_created_at ON candidates(created_at);
"""

# 新增列迁移(老库补列)
MIGRATIONS = [
    ("amazon_rating", "ALTER TABLE candidates ADD COLUMN amazon_rating REAL DEFAULT 0"),
    ("gate_details", "ALTER TABLE candidates ADD COLUMN gate_details TEXT NOT NULL DEFAULT '{}'"),
    ("needs_manual_review", "ALTER TABLE candidates ADD COLUMN needs_manual_review INTEGER DEFAULT 0"),
    ("data_completeness_pct", "ALTER TABLE candidates ADD COLUMN data_completeness_pct REAL DEFAULT 0"),
    ("data_provenance", "ALTER TABLE candidates ADD COLUMN data_provenance TEXT NOT NULL DEFAULT '{}'"),
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # 老库补列
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(candidates)").fetchall()}
        for col, ddl in MIGRATIONS:
            if col not in existing:
                conn.execute(ddl)
        conn.commit()


def upsert_candidate(candidate: ProductCandidate) -> None:
    db_model = CandidateDB.from_candidate(candidate)
    with get_conn() as conn:
        # 动态生成 INSERT, 列与值由 CandidateDB 字段驱动, 避免不同步
        columns = list(CandidateDB.model_fields.keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_sql = ", ".join(columns)
        values = []
        for col in columns:
            v = getattr(db_model, col)
            if isinstance(v, bool):
                v = int(v)
            elif isinstance(v, datetime):
                v = v.isoformat()
            values.append(v)
        conn.execute(
            f"INSERT OR REPLACE INTO candidates ({col_sql}) VALUES ({placeholders})",
            values,
        )
        conn.commit()


def get_candidate(candidate_id: str) -> Optional[ProductCandidate]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if row:
            return CandidateDB(**dict(row)).to_candidate()
    return None


def get_candidates_by_status(status: str, limit: int = 100) -> List[ProductCandidate]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE review_status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
        return [CandidateDB(**dict(row)).to_candidate() for row in rows]


def get_all_candidates(limit: int = 500) -> List[ProductCandidate]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [CandidateDB(**dict(row)).to_candidate() for row in rows]


def update_review_status(candidate_id: str, status: str, notes: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE candidates SET review_status = ?, review_notes = ?, updated_at = ? WHERE id = ?",
            (status, notes, datetime.now().isoformat(), candidate_id)
        )
        conn.commit()


def update_shopify_ids(candidate_id: str, draft_id: Optional[int] = None, product_id: Optional[int] = None) -> None:
    with get_conn() as conn:
        if draft_id is not None:
            conn.execute(
                "UPDATE candidates SET shopify_draft_id = ?, updated_at = ? WHERE id = ?",
                (draft_id, datetime.now().isoformat(), candidate_id)
            )
        if product_id is not None:
            conn.execute(
                "UPDATE candidates SET shopify_product_id = ?, published_at = ?, updated_at = ? WHERE id = ?",
                (product_id, datetime.now().isoformat(), datetime.now().isoformat(), candidate_id)
            )
        conn.commit()