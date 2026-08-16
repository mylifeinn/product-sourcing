from __future__ import annotations

from typing import List
from sourcing.models import ProductCandidate
from sourcing.config import get_config


def _gate_detail(gates: dict, name: str, detail: str) -> dict:
    """记录 gate 详情"""
    gates.setdefault("_details", {})[name] = detail
    return gates


def score_candidate(candidate: ProductCandidate) -> ProductCandidate:
    """Run all 9 hard gates on a candidate.

    三态判定:
    - True  = 通过(有足够真实/估算数据支撑)
    - False = 不通过(有数据但未达标)
    - None  = 数据不足(免费公开数据源拿不到, 不造假; 需人工补充)

    total_score = 通过数 / 9 * 100。数据不足的 gate 不计分, 候选标记 needs_manual_review。
    """
    config = get_config()
    gates = config.scoring
    results = {}

    # ------------------------------------------------------------------
    # Gate 1: Pain Point Keywords(痛点关键词)
    # 免费模式: Google Suggest 关键词 REAL, 但 volume/KD 无免费 API → MISSING。
    # 判定: 有真实 volume/KD 时严格判定; 全部缺失时用
    #       "真实长尾关键词 ≥3 且存在真实趋势信号(上升词/正YoY)" 宽松判定。
    # ------------------------------------------------------------------
    longtail = candidate.longtail_keywords or []
    has_real_volume = any(kw.get("volume_provenance") not in (None, "", "MISSING", "MOCK") for kw in longtail)

    if has_real_volume:
        min_words = gates.gate_1_pain_point_keywords.get("min_longtail_keywords", 3)
        min_vol = gates.gate_1_pain_point_keywords.get("min_monthly_search", 500)
        max_kd = gates.gate_1_pain_point_keywords.get("max_keyword_difficulty", 30)
        qualifying = [
            kw for kw in longtail
            if kw.get("volume", 0) >= min_vol
            and kw.get("kd", 99) <= max_kd
        ]
        results["gate_1"] = len(qualifying) >= min_words
        _gate_detail(results, "gate_1",
                     f"{len(qualifying)}/{min_words} 词达标 (vol≥{min_vol}, kd≤{max_kd}) [REAL]")
    elif len(longtail) >= gates.gate_1_pain_point_keywords.get("min_longtail_keywords", 3) and (
        candidate.google_trends_yoy_pct > 0 or candidate.tiktok_hashtag_growth_pct > 0
    ):
        # 免费模式宽松判定: 真实关键词数 + 真实趋势信号
        results["gate_1"] = True
        _gate_detail(results, "gate_1",
                     f"{len(longtail)} 个真实长尾词 + Trends YoY {candidate.google_trends_yoy_pct:.0f}% "
                     "[关键词REAL, 搜索量/难度MISSING-宽松判定]")
    else:
        results["gate_1"] = False
        _gate_detail(results, "gate_1",
                     f"长尾词 {len(longtail)} 个, 无趋势信号 → 不通过")

    # ------------------------------------------------------------------
    # Gate 2: Trend(趋势)
    # ------------------------------------------------------------------
    trend_pass = (
        candidate.google_trends_yoy_pct >= gates.gate_2_trend.get("google_trends_90d_yoy_min_pct", 20)
        or candidate.tiktok_hashtag_growth_pct >= gates.gate_2_trend.get("tiktok_hashtag_7d_growth_min_pct", 50)
    )
    results["gate_2"] = trend_pass
    _gate_detail(results, "gate_2",
                 f"Google Trends YoY {candidate.google_trends_yoy_pct:.1f}% "
                 f"(需≥{gates.gate_2_trend.get('google_trends_90d_yoy_min_pct', 20)}%) [REAL]")

    # ------------------------------------------------------------------
    # Gate 3: Margin(毛利, ESTIMATED - 基于真实竞品价反推)
    # ------------------------------------------------------------------
    results["gate_3"] = candidate.estimated_margin_pct >= gates.gate_3_margin.get("min_gross_margin_pct", 45)
    _gate_detail(results, "gate_3",
                 f"毛利 {candidate.estimated_margin_pct:.1f}% (需≥{gates.gate_3_margin.get('min_gross_margin_pct', 45)}%) "
                 "[ESTIMATED-反推批发价]")

    # ------------------------------------------------------------------
    # Gate 4: Lightweight(轻便)
    # 重量/尺寸来自 Amazon 详情页(REAL)。缺失(0)时 → 数据不足, 不假通过。
    # ------------------------------------------------------------------
    max_w = gates.gate_4_lightweight.get("max_weight_g", 500)
    max_dims = gates.gate_4_lightweight.get("max_dimensions_cm", [30, 20, 10])
    weight_known = candidate.weight_g > 0
    dims_known = all(d > 0 for d in candidate.dimensions_cm)

    if weight_known and dims_known:
        weight_ok = candidate.weight_g <= max_w
        dims_ok = all(candidate.dimensions_cm[i] <= max_dims[i] for i in range(3))
        results["gate_4"] = weight_ok and dims_ok
        _gate_detail(results, "gate_4",
                     f"{candidate.weight_g:.0f}g / {candidate.dimensions_cm[0]:.0f}×{candidate.dimensions_cm[1]:.0f}×{candidate.dimensions_cm[2]:.0f}cm "
                     f"(需≤{max_w}g, ≤{max_dims}) [REAL-详情页]")
    else:
        results["gate_4"] = None
        _gate_detail(results, "gate_4",
                     "重量/尺寸缺失(详情页未爬到) → 数据不足, 需人工补充 [MISSING]")

    # ------------------------------------------------------------------
    # Gate 5: Quality(质量)
    # 免费模式无供应商数据(1688/AliExpress 需登录)。用 Amazon 竞品评分(REAL)代理:
    # 竞品评分 ≥4.2 视为市场认可的质量信号。供应商评分/退款率/实拍需人工补充。
    # ------------------------------------------------------------------
    min_supplier = gates.gate_5_quality.get("min_supplier_rating", 4.7)
    amazon_rating = getattr(candidate, "amazon_rating", 0.0) or 0.0
    if candidate.supplier_rating > 0:
        quality_pass = (
            candidate.supplier_rating >= min_supplier
            and candidate.refund_rate_pct <= gates.gate_5_quality.get("max_refund_rate_pct", 3)
            and (not gates.gate_5_quality.get("requires_actual_photos", True) or candidate.has_actual_photos)
        )
        results["gate_5"] = quality_pass
        _gate_detail(results, "gate_5",
                     f"供应商评分 {candidate.supplier_rating:.1f} (需≥{min_supplier}), 退款 {candidate.refund_rate_pct:.1f}%")
    elif amazon_rating >= 4.2:
        # Amazon 竞品评分代理(真实)
        results["gate_5"] = True
        _gate_detail(results, "gate_5",
                     f"Amazon 竞品评分 {amazon_rating:.1f}≥4.2 [REAL-代理]; 供应商评分/退款率需人工补充")
    else:
        results["gate_5"] = None
        _gate_detail(results, "gate_5",
                     "供应商数据缺失且无竞品评分 → 数据不足 [MISSING]")

    # ------------------------------------------------------------------
    # Gate 6: Uniqueness(独特性)
    # 免费模式无 TEMU/SHEIN 反爬数据, 无法真实验证前3页无同款 → 数据不足。
    # 仅当 uniqueness_passed 被显式设置(如人工审核)才通过。
    # ------------------------------------------------------------------
    if candidate.uniqueness_passed:
        results["gate_6"] = True
        _gate_detail(results, "gate_6", "人工确认无同款")
    else:
        results["gate_6"] = None
        _gate_detail(results, "gate_6",
                     "免费模式无法自动检测 TEMU/SHEIN 同款 → 需人工在审核阶段确认 [MISSING]")

    # ------------------------------------------------------------------
    # Gate 7: Seasonality(长青/季节)
    # ------------------------------------------------------------------
    if candidate.is_evergreen:
        results["gate_7"] = True
        _gate_detail(results, "gate_7", "长青品类 (默认, 人工可复核)")
    else:
        seasonal_ok = (
            candidate.seasonal_peak_window_days >= gates.gate_7_seasonality.get("seasonal_min_peak_window_days", 90)
            and candidate.prep_lead_time_days <= gates.gate_7_seasonality.get("seasonal_prep_lead_time_days", 60)
        )
        results["gate_7"] = seasonal_ok
        _gate_detail(results, "gate_7",
                     f"季节性: 旺季 {candidate.seasonal_peak_window_days}天, 备货 {candidate.prep_lead_time_days}天")

    # ------------------------------------------------------------------
    # Gate 8: Market Proof(市场验证)
    # 数据来源: bought-in-past-month×3 [REAL徽章] / BSR估算 [ESTIMATED] / 评论数 [REAL]
    # ------------------------------------------------------------------
    min_sales = gates.gate_8_market_proof.get("min_competitor_sales_90d", 50)
    min_reviews = gates.gate_8_market_proof.get("min_competitor_reviews", 200)
    proof_pass = (
        candidate.competitor_sales_90d >= min_sales
        or candidate.competitor_reviews >= min_reviews
    )
    results["gate_8"] = proof_pass
    _gate_detail(results, "gate_8",
                 f"竞品90天销量 {candidate.competitor_sales_90d} (需≥{min_sales}), 评论 {candidate.competitor_reviews} (需≥{min_reviews})")

    # ------------------------------------------------------------------
    # Gate 9: Customer Value(客户价值)
    # AOV 用竞品价格 [REAL]; LTV/复购周期免费模式无公开数据 → 数据不足标注。
    # ------------------------------------------------------------------
    aov_pass = candidate.estimated_aov_usd >= gates.gate_9_customer_value.get("min_aov_usd", 60)
    ltv_known = candidate.estimated_ltv_orders > 0
    repurchase_known = candidate.estimated_repurchase_cycle_days > 0
    if ltv_known and repurchase_known:
        value_pass = (
            aov_pass
            and candidate.estimated_repurchase_cycle_days <= gates.gate_9_customer_value.get("max_repurchase_cycle_days", 90)
            and candidate.estimated_ltv_orders >= gates.gate_9_customer_value.get("min_ltv_orders", 3)
        )
        results["gate_9"] = value_pass
    else:
        # 只有 AOV 可判定(竞品价 REAL)
        results["gate_9"] = aov_pass
    _gate_detail(results, "gate_9",
                 f"AOV ${candidate.estimated_aov_usd:.0f} (需≥${gates.gate_9_customer_value.get('min_aov_usd', 60)}); "
                 f"LTV/复购 {'缺失-需人工' if not (ltv_known and repurchase_known) else '已评估'}")

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    candidate.gate_results = {k: v for k, v in results.items() if k != "_details"}
    candidate.gate_details = results.get("_details", {})

    passed = sum(1 for v in candidate.gate_results.values() if v is True)
    missing = sum(1 for v in candidate.gate_results.values() if v is None)

    candidate.total_score = round(passed / 9 * 100)
    candidate.passed_all_gates = passed == 9 and missing == 0
    candidate.needs_manual_review = missing > 0 or any(
        "MISSING" in d or "人工" in d for d in candidate.gate_details.values()
    )

    # 数据完整度: 逐字段标注 REAL / ESTIMATED / MISSING(绝不造假)
    candidate.data_provenance = build_provenance(candidate)
    prov = candidate.data_provenance
    real_est = sum(1 for v in prov.values() if v in ("REAL", "ESTIMATED"))
    candidate.data_completeness_pct = round(real_est / len(prov) * 100) if prov else 0

    return candidate


def build_provenance(c: ProductCandidate) -> dict[str, str]:
    """逐字段标注数据来源真实度。

    REAL      = 公开页面直接抓取(标题/价格/评论/评分/BSR/重量/尺寸/关键词/趋势)
    ESTIMATED = 基于真实输入的可解释推算(销量: 徽章×3 或 BSR 估算表; 批发价: 零售价反推)
    MISSING   = 免费公开渠道无数据, 不造假(搜索量/难度/供应商评分/退款率/LTV)
    """
    p: dict[str, str] = {}

    p["title"] = "REAL"
    p["competitor_price"] = "REAL" if c.estimated_aov_usd > 0 else "MISSING"
    p["competitor_reviews"] = "REAL" if c.competitor_reviews > 0 else "MISSING"
    p["amazon_rating"] = "REAL" if c.amazon_rating > 0 else "MISSING"
    p["competitor_sales_90d"] = (
        "ESTIMATED" if c.competitor_sales_90d > 0 else "MISSING"
    )  # 徽章×3(基于REAL徽章) 或 BSR估算表
    p["google_trends_yoy"] = "REAL" if c.google_trends_yoy_pct != 0 else "MISSING"
    p["keywords"] = "REAL" if c.longtail_keywords else "MISSING"
    p["keyword_volume_kd"] = (
        "REAL" if any(k.get("volume_provenance") == "REAL" for k in c.longtail_keywords)
        else "MISSING"  # 无免费搜索量 API
    )
    p["weight_dimensions"] = (
        "REAL" if c.weight_g > 0 and all(d > 0 for d in c.dimensions_cm) else "MISSING"
    )
    p["wholesale_price"] = (
        "REAL" if c.wholesale_price_usd > 0 and getattr(c, "source", "") not in ("amazon", "amazon_real", "public")
        else ("ESTIMATED" if c.wholesale_price_usd > 0 else "MISSING")
    )  # 真实批发报价 vs 零售价反推
    p["margin"] = "ESTIMATED" if c.estimated_margin_pct != 0 else "MISSING"
    p["supplier_rating"] = "REAL" if c.supplier_rating > 0 else "MISSING"
    p["ltv_repurchase"] = (
        "ESTIMATED" if c.estimated_ltv_orders > 0 and c.estimated_repurchase_cycle_days > 0 else "MISSING"
    )
    return p


def score_candidates(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
    """Score a list of candidates"""
    return [score_candidate(c) for c in candidates]
