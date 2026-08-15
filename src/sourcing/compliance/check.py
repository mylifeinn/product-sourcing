from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Tuple
from sourcing.models import ProductCandidate
from sourcing.config import get_config


CACHE_DB = Path(__file__).parent.parent.parent / "data" / "compliance_cache.db"
CACHE_DB.parent.mkdir(parents=True, exist_ok=True)


CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS compliance_cache (
    query_hash TEXT PRIMARY KEY,
    query_text TEXT NOT NULL,
    patent_risk_level TEXT NOT NULL,
    trademark_risk_level TEXT NOT NULL,
    matched_patents TEXT NOT NULL,
    matched_trademarks TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
"""


@contextmanager
def get_cache_conn():
    conn = sqlite3.connect(CACHE_DB)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_compliance_cache():
    with get_cache_conn() as conn:
        conn.executescript(CACHE_SCHEMA)
        conn.commit()


def _hash_query(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def check_cache(query: str) -> Tuple[str, str, List[str], List[str]] | None:
    """Check compliance cache, return (patent_risk, trademark_risk, patents, trademarks) or None"""
    config = get_config()
    ttl_days = config.compliance.cache_ttl_days
    query_hash = _hash_query(query)
    
    with get_cache_conn() as conn:
        row = conn.execute(
            "SELECT * FROM compliance_cache WHERE query_hash = ?", (query_hash,)
        ).fetchone()
        
        if row:
            checked_at = datetime.fromisoformat(row["checked_at"])
            if datetime.now() - checked_at < timedelta(days=ttl_days):
                return (
                    row["patent_risk_level"],
                    row["trademark_risk_level"],
                    json.loads(row["matched_patents"]),
                    json.loads(row["matched_trademarks"]),
                )
    return None


def save_cache(query: str, patent_risk: str, trademark_risk: str, patents: List[str], trademarks: List[str]):
    """Save compliance check result to cache"""
    query_hash = _hash_query(query)
    with get_cache_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO compliance_cache 
            (query_hash, query_text, patent_risk_level, trademark_risk_level, matched_patents, matched_trademarks, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            query_hash, query, patent_risk, trademark_risk,
            json.dumps(patents), json.dumps(trademarks), datetime.now().isoformat()
        ))
        conn.commit()


def check_patents(keywords: List[str]) -> Tuple[str, List[str]]:
    """
    Check patent risk for keywords.
    Returns (risk_level, matched_patent_ids)
    Risk levels: none, low, high
    """
    # TODO: Implement real Google Patents / USPTO API calls
    # For now, return mock results based on keyword patterns
    
    high_risk_terms = ["patented", "patent pending", "独家专利", "发明专利", "实用新型"]
    matched = []
    
    for kw in keywords:
        kw_lower = kw.lower()
        for risk_term in high_risk_terms:
            if risk_term in kw_lower:
                matched.append(f"MOCK-PATENT-{kw[:20]}")
    
    if matched:
        return "high", matched
    elif any("unique" in kw.lower() or "innovative" in kw.lower() for kw in keywords):
        return "low", []
    return "none", []


def check_trademarks(keywords: List[str]) -> Tuple[str, List[str]]:
    """
    Check trademark risk for keywords.
    Returns (risk_level, matched_trademark_ids)
    """
    # TODO: Implement real USPTO TESS / TMview API calls
    # For now, return mock results
    
    high_risk_terms = ["brand", "trademark", "®", "™", "商标", "品牌"]
    matched = []
    
    for kw in keywords:
        kw_lower = kw.lower()
        for risk_term in high_risk_terms:
            if risk_term in kw_lower:
                matched.append(f"MOCK-TM-{kw[:20]}")
    
    if matched:
        return "high", matched
    return "none", []


def run_compliance_check(candidate: ProductCandidate) -> ProductCandidate:
    """Run compliance check on candidate, update patent/trademark risk fields"""
    # Build query from relevant fields
    config = get_config()
    patent_kws = []
    trademark_kws = []
    
    for field in config.compliance.patent_keywords:
        if field == "title":
            patent_kws.append(candidate.title)
        elif field == "niche":
            patent_kws.append(candidate.niche)
        elif field == "pain_point_keywords":
            patent_kws.extend(candidate.pain_point_keywords)
    
    for field in config.compliance.trademark_keywords:
        if field == "title":
            trademark_kws.append(candidate.title)
        elif field == "brand":
            trademark_kws.append(candidate.niche)  # niche as proxy for brand
    
    query = " ".join(patent_kws + trademark_kws)
    
    # Check cache first
    cached = check_cache(query)
    if cached:
        candidate.patent_risk_level, candidate.trademark_risk_level, candidate.matched_patents, _ = cached
        return candidate
    
    # Run checks
    patent_risk, patents = check_patents(patent_kws)
    trademark_risk, trademarks = check_trademarks(trademark_kws)
    
    candidate.patent_risk_level = patent_risk
    candidate.trademark_risk_level = trademark_risk
    candidate.matched_patents = patents
    
    # Save to cache
    save_cache(query, patent_risk, trademark_risk, patents, trademarks)
    
    return candidate


def run_compliance_checks(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
    """Run compliance checks on list of candidates"""
    return [run_compliance_check(c) for c in candidates]