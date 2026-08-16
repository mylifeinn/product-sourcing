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
    amazon_bsr INTEGER DEFAULT 0,
    amazon_result_count INTEGER DEFAULT 0,
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
    amazon_duplicate_count INTEGER DEFAULT -1,
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

-- BSR 快照历史: 同一 ASIN 多次抓取, 用于 Gate2 单品趋势判定(BSR 改善=上升)
CREATE TABLE IF NOT EXISTS bsr_history (
    asin TEXT NOT NULL,
    bsr INTEGER NOT NULL,
    sales_90d INTEGER DEFAULT 0,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (asin, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_bsr_asin ON bsr_history(asin);
"""

# 新增列迁移(老库补列)
MIGRATIONS = [
    ("amazon_rating", "ALTER TABLE candidates ADD COLUMN amazon_rating REAL DEFAULT 0"),
    ("gate_details", "ALTER TABLE candidates ADD COLUMN gate_details TEXT NOT NULL DEFAULT '{}'"),
    ("needs_manual_review", "ALTER TABLE candidates ADD COLUMN needs_manual_review INTEGER DEFAULT 0"),
    ("data_completeness_pct", "ALTER TABLE candidates ADD COLUMN data_completeness_pct REAL DEFAULT 0"),
    ("data_provenance", "ALTER TABLE candidates ADD COLUMN data_provenance TEXT NOT NULL DEFAULT '{}'"),
    ("amazon_bsr", "ALTER TABLE candidates ADD COLUMN amazon_bsr INTEGER DEFAULT 0"),
    ("amazon_result_count", "ALTER TABLE candidates ADD COLUMN amazon_result_count INTEGER DEFAULT 0"),
    ("amazon_duplicate_count", "ALTER TABLE candidates ADD COLUMN amazon_duplicate_count INTEGER DEFAULT -1"),
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


# ----------------------------------------------------------------------
# BSR 快照历史(Gate2 单品趋势: BSR 改善 = 排名上升)
# ----------------------------------------------------------------------
def record_bsr_snapshot(asin: str, bsr: int, sales_90d: int = 0) -> None:
    """记录一次 BSR 快照。同一天重复抓取覆盖, 避免噪声。"""
    if not asin or bsr <= 0:
        return
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO bsr_history (asin, bsr, sales_90d, captured_at)
               VALUES (?, ?, ?, ?)""",
            (asin, bsr, sales_90d, today),
        )
        conn.commit()


def get_bsr_history(asin: str, min_span_days: int = 14) -> list[dict]:
    """按时间顺序返回 ASIN 的 BSR 历史; 只返回跨度足够(≥min_span_days)的记录。

    返回 [{asin, bsr, sales_90d, captured_at}, ...], 不足 2 条或跨度不够返回 []。
    """
    if not asin:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT asin, bsr, sales_90d, captured_at FROM bsr_history WHERE asin = ? ORDER BY captured_at ASC",
            (asin,),
        ).fetchall()
    if len(rows) < 2:
        return []
    from datetime import date
    dates = [date.fromisoformat(r["captured_at"]) for r in rows]
    if (dates[-1] - dates[0]).days < min_span_days:
        return []
    return [dict(r) for r in rows]