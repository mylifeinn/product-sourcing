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
    estimated_aov_usd REAL DEFAULT 0,
    estimated_repurchase_cycle_days INTEGER DEFAULT 0,
    estimated_ltv_orders REAL DEFAULT 0,
    patent_risk_level TEXT DEFAULT 'none',
    trademark_risk_level TEXT DEFAULT 'none',
    matched_patents TEXT NOT NULL,
    gate_results TEXT NOT NULL,
    total_score INTEGER DEFAULT 0,
    passed_all_gates INTEGER DEFAULT 0,
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
        conn.commit()


def upsert_candidate(candidate: ProductCandidate) -> None:
    db_model = CandidateDB.from_candidate(candidate)
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO candidates (
                id, title, niche, pain_point_keywords, trend_score, longtail_keywords,
                google_trends_yoy_pct, tiktok_hashtag_growth_pct, wholesale_price_usd,
                estimated_retail_price_usd, estimated_shipping_usd, estimated_margin_pct,
                weight_g, dimensions_cm, shipping_channel, supplier_rating, refund_rate_pct,
                has_actual_photos, supplier_url, supplier_contact, uniqueness_passed,
                competitor_urls, is_evergreen, seasonal_peak_window_days, prep_lead_time_days,
                competitor_sales_90d, competitor_reviews, market_proof_urls,
                estimated_aov_usd, estimated_repurchase_cycle_days, estimated_ltv_orders,
                patent_risk_level, trademark_risk_level, matched_patents, gate_results,
                total_score, passed_all_gates, review_status, review_notes,
                shopify_draft_id, shopify_product_id, published_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            db_model.id,
            db_model.title,
            db_model.niche,
            db_model.pain_point_keywords,
            db_model.trend_score,
            db_model.longtail_keywords,
            db_model.google_trends_yoy_pct,
            db_model.tiktok_hashtag_growth_pct,
            db_model.wholesale_price_usd,
            db_model.estimated_retail_price_usd,
            db_model.estimated_shipping_usd,
            db_model.estimated_margin_pct,
            db_model.weight_g,
            db_model.dimensions_cm,
            db_model.shipping_channel,
            db_model.supplier_rating,
            db_model.refund_rate_pct,
            int(db_model.has_actual_photos),
            db_model.supplier_url,
            db_model.supplier_contact,
            int(db_model.uniqueness_passed),
            db_model.competitor_urls,
            int(db_model.is_evergreen),
            db_model.seasonal_peak_window_days,
            db_model.prep_lead_time_days,
            db_model.competitor_sales_90d,
            db_model.competitor_reviews,
            db_model.market_proof_urls,
            db_model.estimated_aov_usd,
            db_model.estimated_repurchase_cycle_days,
            db_model.estimated_ltv_orders,
            db_model.patent_risk_level,
            db_model.trademark_risk_level,
            db_model.matched_patents,
            db_model.gate_results,
            db_model.total_score,
            int(db_model.passed_all_gates),
            db_model.review_status,
            db_model.review_notes,
            db_model.shopify_draft_id,
            db_model.shopify_product_id,
            db_model.published_at.isoformat() if db_model.published_at else None,
            db_model.created_at.isoformat(),
            db_model.updated_at.isoformat(),
        ))
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