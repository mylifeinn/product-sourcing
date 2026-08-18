from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawProductData:
    """Raw data fetched from various sources before normalization"""
    title: str
    niche: str
    source: str  # "1688", "tiktok", "competitor", "manual", "public", "real"
    source_url: str
    wholesale_price_cny: float = 0.0
    weight_g: float = 0.0
    dimensions_cm: tuple[float, float, float] = (0, 0, 0)
    supplier_rating: float = 0.0
    refund_rate_pct: float = 0.0
    has_actual_photos: bool = False
    supplier_contact: str = ""
    # Keyword data
    longtail_keywords: List[dict] = field(default_factory=list)
    google_trends_yoy_pct: float = 0.0
    tiktok_hashtag_growth_pct: float = 0.0
    # Amazon 单品数据 [REAL]
    asin: str = ""  # Amazon ASIN, 用于 BSR 历史追踪
    amazon_bsr: int = 0  # Best Sellers Rank(详情页 REAL)
    amazon_result_count: int = 0  # niche 搜索结果总数(搜索页 REAL, volume 代理)
    # Competitor data
    competitor_sales_90d: int = 0
    competitor_reviews: int = 0
    competitor_urls: List[str] = field(default_factory=list)
    amazon_rating: float = 0.0  # Amazon 竞品评分 [REAL]
    # Seasonality
    is_evergreen: bool = True
    seasonal_peak_window_days: int = 0
    prep_lead_time_days: int = 0
    # Customer value estimates
    estimated_aov_usd: float = 0.0
    estimated_repurchase_cycle_days: int = 0
    estimated_ltv_orders: float = 0.0
    # Cost estimate (for public fetcher)
    cost_estimate: Optional[object] = None
    # Reddit signals (REAL 用户讨论)
    reddit_pain_points: List[dict] = field(default_factory=list)
    reddit_recommendations: List[dict] = field(default_factory=list)
    reddit_complaints: List[dict] = field(default_factory=list)