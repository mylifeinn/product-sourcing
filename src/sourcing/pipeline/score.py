from __future__ import annotations

import re
from typing import List
from sourcing.models import ProductCandidate
from sourcing.config import get_config
from sourcing.database import get_bsr_history


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
    # 判定优先级:
    #   1. 有真实 volume/KD → 严格判定
    #   2. 无 volume 但有 Amazon 搜索结果总数(REAL volume 代理, 与月搜索量强相关)
    #      → result_count ≥ min_monthly_search 且长尾词数达标 → 通过; 否则不通过
    #   3. 全缺失 → 宽松判定(真实关键词数 + 真实趋势信号), 标注需复核
    # ------------------------------------------------------------------
    longtail = candidate.longtail_keywords or []
    has_real_volume = any(kw.get("volume_provenance") not in (None, "", "MISSING", "MOCK") for kw in longtail)
    min_words = gates.gate_1_pain_point_keywords.get("min_longtail_keywords", 3)
    min_vol = gates.gate_1_pain_point_keywords.get("min_monthly_search", 500)
    max_kd = gates.gate_1_pain_point_keywords.get("max_keyword_difficulty", 30)

    if has_real_volume:
        qualifying = [
            kw for kw in longtail
            if kw.get("volume", 0) >= min_vol
            and kw.get("kd", 99) <= max_kd
        ]
        results["gate_1"] = len(qualifying) >= min_words
        _gate_detail(results, "gate_1",
                     f"{len(qualifying)}/{min_words} 词达标 (vol≥{min_vol}, kd≤{max_kd}) [REAL]")
    elif candidate.amazon_result_count > 0:
        # volume 代理: Amazon 搜索结果总数是 REAL 且与搜索量强相关
        count_ok = candidate.amazon_result_count >= min_vol
        kws_ok = len(longtail) >= min_words
        results["gate_1"] = count_ok and kws_ok
        _gate_detail(results, "gate_1",
                     f"Amazon 搜索词结果 {candidate.amazon_result_count} (代理 vol≥{min_vol}: "
                     f"{'✅' if count_ok else '❌'}), 长尾词 {len(longtail)}/{min_words} "
                     "[volume=REAL代理, 长尾词=REAL]")
    elif len(longtail) >= min_words and (
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
                     f"长尾词 {len(longtail)} 个, 无 volume/结果数/趋势信号 → 不通过")

    # ------------------------------------------------------------------
    # Gate 2: Trend(趋势) — 多通道解耦
    # 通道1(REAL): 头词 Google Trends YoY ≥ 20%
    # 通道2(REAL): Trends rising queries 中显著上升长尾词 ≥ N 个(头词跌但长尾涨 → 过)
    # 通道3(REAL): BSR 快照历史改善 ≥ 30%(单品排名上升 → 过; 恶化 → 明确不通过)
    # 数据缺失(无趋势信号、无上升词、无 BSR 历史) → None, 需人工
    # ------------------------------------------------------------------
    trend_cfg = gates.gate_2_trend
    yoy_min = trend_cfg.get("google_trends_90d_yoy_min_pct", 20)
    min_rising_kws = trend_cfg.get("min_rising_keywords", 2)
    min_rising_val = trend_cfg.get("min_rising_value", 100)
    bsr_improve_pct = trend_cfg.get("bsr_improvement_pct", 30) / 100.0
    bsr_min_span = trend_cfg.get("bsr_min_span_days", 14)

    yoy = candidate.google_trends_yoy_pct
    rising_kws = [
        kw for kw in (candidate.longtail_keywords or [])
        if kw.get("trending_provenance") == "REAL"
    ]
    significant_rising = [
        kw for kw in rising_kws
        if kw.get("trending_value", 0) >= min_rising_val
    ]

    if yoy >= yoy_min:
        results["gate_2"] = True
        _gate_detail(results, "gate_2",
                     f"头词 Google Trends YoY {yoy:.1f}% ≥ {yoy_min}% [REAL]")
    elif len(significant_rising) >= min_rising_kws:
        results["gate_2"] = True
        _gate_detail(results, "gate_2",
                     f"头词 YoY {yoy:.1f}%(未达{yoy_min}%), 但上升长尾词 "
                     f"{len(significant_rising)}/{min_rising_kws} 个显著上涨 "
                     f"(值≥{min_rising_val}) [REAL-长尾通道]")
    else:
        # BSR 历史通道(需跨日快照, cron 跑几天后自动生效)
        asin = ""
        if candidate.competitor_urls:
            m = re.search(r'/dp/([A-Z0-9]{10})', candidate.competitor_urls[0])
            asin = m.group(1) if m else ""
        bsr_history = get_bsr_history(asin, min_span_days=bsr_min_span) if asin else []

        if len(bsr_history) >= 2:
            old_bsr = bsr_history[0]["bsr"]
            new_bsr = bsr_history[-1]["bsr"]
            improvement = (old_bsr - new_bsr) / old_bsr if old_bsr > 0 else 0.0
            results["gate_2"] = improvement >= bsr_improve_pct
            _gate_detail(results, "gate_2",
                         f"BSR {old_bsr}→{new_bsr} 改善 {improvement*100:.0f}% "
                         f"(需≥{bsr_improve_pct*100:.0f}%) [REAL-BSR历史]")
        elif yoy < 0:
            # 明确下跌: 头词跌且无显著上升词/BSR 改善
            results["gate_2"] = False
            _gate_detail(results, "gate_2",
                         f"头词 YoY {yoy:.1f}%(下跌), 上升词 {len(significant_rising)} 个, "
                         "无 BSR 历史 → 不通过 [REAL]")
        elif yoy > 0 and not rising_kws:
            results["gate_2"] = False
            _gate_detail(results, "gate_2",
                         f"头词 YoY {yoy:.1f}% 但未达 {yoy_min}%, 无上升词数据 → 不通过 [REAL]")
        elif yoy == 0 and not rising_kws:
            # 无任何趋势信号 → 数据不足, 不武断判定
            results["gate_2"] = None
            _gate_detail(results, "gate_2",
                         "Trends 数据缺失(YoY=0 且无上升词) → 数据不足, 需人工 [MISSING]")
        else:
            results["gate_2"] = False
            _gate_detail(results, "gate_2",
                         f"头词 YoY {yoy:.1f}%(未达{yoy_min}%), 上升词 {len(significant_rising)} 个 "
                         f"(需≥{min_rising_kws}) → 不通过 [REAL]")

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
    # Amazon: 自动同款检测(REAL, 前3页标题相似度匹配, 排除自身 ASIN)。
    # TEMU/SHEIN 登录墙无法自动检测 → 人工确认; uniqueness_passed 可人工置 True。
    # ------------------------------------------------------------------
    if candidate.amazon_duplicate_count >= 0:
        max_match = gates.gate_6_uniqueness.get("max_matching_pages", 3)
        results["gate_6"] = candidate.amazon_duplicate_count <= max_match
        _gate_detail(results, "gate_6",
                     f"Amazon 前3页同款 {candidate.amazon_duplicate_count} 个 (阈值≤{max_match}) "
                     "[REAL-自动检测]; TEMU/SHEIN 需人工")
    elif candidate.uniqueness_passed:
        results["gate_6"] = True
        _gate_detail(results, "gate_6", "人工确认无同款")
    else:
        results["gate_6"] = None
        _gate_detail(results, "gate_6",
                     "Amazon 同款检测未执行且无人工确认 → 数据不足 [MISSING]")

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
    p["amazon_result_count"] = "REAL" if c.amazon_result_count > 0 else "MISSING"
    p["amazon_bsr"] = "REAL" if c.amazon_bsr > 0 else "MISSING"
    p["amazon_uniqueness"] = "REAL" if c.amazon_duplicate_count >= 0 else "MISSING"
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
