from __future__ import annotations

from typing import List
from sourcing.models import ProductCandidate
from sourcing.config import get_config


def score_candidate(candidate: ProductCandidate) -> ProductCandidate:
    """Run all 9 hard gates on a candidate, update gate_results and total_score"""
    config = get_config()
    gates = config.scoring
    
    results = {}
    
    # Gate 1: Pain Point Keywords
    longtail = candidate.longtail_keywords
    qualifying = [
        kw for kw in longtail
        if kw.get("volume", 0) >= gates.gate_1_pain_point_keywords.get("min_longtail_keywords", 3)
        and kw.get("volume", 0) >= gates.gate_1_pain_point_keywords.get("min_monthly_search", 500)
        and kw.get("kd", 99) <= gates.gate_1_pain_point_keywords.get("max_keyword_difficulty", 30)
    ]
    results["gate_1"] = len(qualifying) >= gates.gate_1_pain_point_keywords.get("min_longtail_keywords", 3)
    
    # Gate 2: Trend
    trend_pass = (
        candidate.google_trends_yoy_pct >= gates.gate_2_trend.get("google_trends_90d_yoy_min_pct", 20)
        or candidate.tiktok_hashtag_growth_pct >= gates.gate_2_trend.get("tiktok_hashtag_7d_growth_min_pct", 50)
    )
    results["gate_2"] = trend_pass
    
    # Gate 3: Margin
    margin_pass = candidate.estimated_margin_pct >= gates.gate_3_margin.get("min_gross_margin_pct", 45)
    results["gate_3"] = margin_pass
    
    # Gate 4: Lightweight
    weight_ok = candidate.weight_g <= gates.gate_4_lightweight.get("max_weight_g", 500)
    dims_ok = all(
        candidate.dimensions_cm[i] <= gates.gate_4_lightweight.get("max_dimensions_cm", [30, 20, 10])[i]
        for i in range(3)
    )
    channel_ok = candidate.shipping_channel in gates.gate_4_lightweight.get("allowed_shipping_channels", ["ePacket", "4PX", "YunExpress"])
    results["gate_4"] = weight_ok and dims_ok and (channel_ok or candidate.shipping_channel == "")
    
    # Gate 5: Quality
    quality_pass = (
        candidate.supplier_rating >= gates.gate_5_quality.get("min_supplier_rating", 4.7)
        and candidate.refund_rate_pct <= gates.gate_5_quality.get("max_refund_rate_pct", 3)
        and (not gates.gate_5_quality.get("requires_actual_photos", True) or candidate.has_actual_photos)
    )
    results["gate_5"] = quality_pass
    
    # Gate 6: Uniqueness
    results["gate_6"] = candidate.uniqueness_passed
    
    # Gate 7: Seasonality
    if candidate.is_evergreen:
        # For evergreen, we just check it's marked as evergreen
        results["gate_7"] = True
    else:
        # Seasonal: must have sufficient peak window and prep time
        seasonal_ok = (
            candidate.seasonal_peak_window_days >= gates.gate_7_seasonality.get("seasonal_min_peak_window_days", 90)
            and candidate.prep_lead_time_days <= gates.gate_7_seasonality.get("seasonal_prep_lead_time_days", 60)
        )
        results["gate_7"] = seasonal_ok
    
    # Gate 8: Market Proof
    proof_pass = (
        candidate.competitor_sales_90d >= gates.gate_8_market_proof.get("min_competitor_sales_90d", 50)
        or candidate.competitor_reviews >= gates.gate_8_market_proof.get("min_competitor_reviews", 200)
    )
    results["gate_8"] = proof_pass
    
    # Gate 9: Customer Value
    value_pass = (
        candidate.estimated_aov_usd >= gates.gate_9_customer_value.get("min_aov_usd", 60)
        and candidate.estimated_repurchase_cycle_days <= gates.gate_9_customer_value.get("max_repurchase_cycle_days", 90)
        and candidate.estimated_ltv_orders >= gates.gate_9_customer_value.get("min_ltv_orders", 3)
    )
    results["gate_9"] = value_pass
    
    # Calculate total score (100 if all pass, 0 if any fail - Hard Gate system)
    all_passed = all(results.values())
    candidate.gate_results = results
    candidate.total_score = 100 if all_passed else 0
    candidate.passed_all_gates = all_passed
    
    return candidate


def score_candidates(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
    """Score a list of candidates"""
    return [score_candidate(c) for c in candidates]