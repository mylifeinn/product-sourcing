from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sourcing.models import ProductCandidate
from sourcing.database import get_all_candidates, upsert_candidate
from sourcing.config import get_config
from sourcing.notion.sync import NotionSync


def calculate_health(candidate: ProductCandidate, days_since_publish: int) -> Dict[str, Any]:
    """Calculate health metrics for a published product"""
    config = get_config()
    thresholds = config.healthcheck.thresholds
    
    # Determine which threshold window applies
    if days_since_publish <= 7:
        window = "t7"
    elif days_since_publish <= 14:
        window = "t14"
    else:
        window = "t30"
    
    # Mock data - in production, fetch from Shopify/GA4 APIs
    # TODO: Implement real data fetching
    mock_metrics = {
        "sessions": 45,
        "add_to_cart_rate_pct": 2.5,
        "checkout_rate_pct": 0.8,
        "cvr_pct": 0.4,
        "return_rate_pct": 12.0,
        "aov_usd": 55.0,
        "ltv_orders": 1.2,
    }
    
    # Check thresholds
    alerts = []
    
    if mock_metrics["sessions"] < thresholds.sessions.get(window, 0):
        alerts.append(f"低流量: {mock_metrics['sessions']} < {thresholds.sessions.get(window, 0)}")
    
    if mock_metrics["add_to_cart_rate_pct"] < thresholds.add_to_cart_rate_pct.get(window, 0):
        alerts.append(f"详情页弱: 加购率 {mock_metrics['add_to_cart_rate_pct']}% < {thresholds.add_to_cart_rate_pct.get(window, 0)}%")
    
    if mock_metrics["checkout_rate_pct"] < thresholds.checkout_rate_pct.get(window, 0):
        alerts.append(f"信任/价格弱: 结账率 {mock_metrics['checkout_rate_pct']}% < {thresholds.checkout_rate_pct.get(window, 0)}%")
    
    if mock_metrics["cvr_pct"] < thresholds.cvr_pct.get(window, 0):
        alerts.append(f"整体弱: CVR {mock_metrics['cvr_pct']}% < {thresholds.cvr_pct.get(window, 0)}%")
    
    if mock_metrics["return_rate_pct"] > thresholds.return_rate_pct.get(window, 100):
        alerts.append(f"质量/描述不符: 退货率 {mock_metrics['return_rate_pct']}% > {thresholds.return_rate_pct.get(window, 100)}%")
    
    if window in ["t14", "t30"] and mock_metrics["aov_usd"] < thresholds.min_aov_usd.get(window, 0):
        alerts.append(f"低价值: AOV ${mock_metrics['aov_usd']} < ${thresholds.min_aov_usd.get(window, 0)}")
    
    if window == "t30" and mock_metrics["ltv_orders"] < thresholds.min_ltv_orders.get(window, 0):
        alerts.append(f"低复购: LTV {mock_metrics['ltv_orders']}单 < {thresholds.min_ltv_orders.get(window, 0)}单")
    
    # Determine health label
    if not alerts:
        health_label = "健康"
    elif len(alerts) <= 2:
        health_label = "需关注"
    else:
        health_label = "待优化"
    
    return {
        "window": window,
        "days_since_publish": days_since_publish,
        "metrics": mock_metrics,
        "alerts": alerts,
        "health_label": health_label,
    }


def run_healthcheck(days: int = 30) -> List[Dict[str, Any]]:
    """Run healthcheck on all published products within N days"""
    candidates = get_all_candidates(limit=1000)
    results = []
    
    notion = NotionSync()
    
    for candidate in candidates:
        if candidate.published_at and candidate.review_status == "approved":
            days_since = (datetime.now() - candidate.published_at).days
            if days_since <= days:
                health = calculate_health(candidate, days_since)
                health["candidate_id"] = candidate.id
                health["candidate_title"] = candidate.title
                results.append(health)
                
                # Update Notion with health label
                if notion.client:
                    page = notion._find_page_by_id(candidate.id)
                    if page:
                        try:
                            notion.client.pages.update(
                                page_id=page["id"],
                                properties={"健康度": {"select": {"name": health["health_label"]}}}
                            )
                        except Exception:
                            pass
    
    return results


def send_alerts(health_results: List[Dict[str, Any]]) -> None:
    """Send alerts for unhealthy products (Telegram/Slack)"""
    # TODO: Implement Telegram/Slack notification
    for h in health_results:
        if h["health_label"] in ["需关注", "待优化"]:
            print(f"⚠️ ALERT: {h['candidate_title']} ({h['candidate_id']}) - {h['health_label']}")
            for alert in h["alerts"]:
                print(f"  - {alert}")