from __future__ import annotations

import asyncio
from typing import List, Optional

from sourcing.models import ProductCandidate
from sourcing.config import get_config
from sourcing.database import upsert_candidate, record_bsr_snapshot
from sourcing.pipeline.cost_estimator import CostEstimator
from sourcing.pipeline.raw_data import RawProductData
from sourcing.pipeline.wholesale_fetch import fetch_wholesale_offers
from sourcing.pipeline.public_fetch import fetch_niche_public, PublicFetcher, check_amazon_duplicates_batch


class BaseFetcher:
    """Base class for all data fetchers"""

    def __init__(self):
        self.config = get_config()

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        raise NotImplementedError


class MockFetcher(BaseFetcher):
    """开发/测试用假数据 —— 仅用于验证管线流程, 绝不用于真实选品。

    真实选品请使用 --source public 或 --source real(纯公开真实数据)。
    """

    MOCK_PRODUCTS = {
        "portable neck massager": [
            {"title": "Portable Neck Massager Wireless Heat EMS", "price_usd": 39.99, "weight": 180, "dims": (15, 10, 5)},
            {"title": "Neck Massager Home Office Multi-Mode", "price_usd": 49.99, "weight": 220, "dims": (18, 12, 6)},
        ],
        "magnetic cable organizer": [
            {"title": "Magnetic Cable Organizer Desktop", "price_usd": 19.99, "weight": 35, "dims": (8, 5, 2)},
            {"title": "Strong Magnetic Cable Clip Multi Slot", "price_usd": 24.99, "weight": 42, "dims": (10, 6, 3)},
        ],
        "foldable silicone water bottle": [
            {"title": "Foldable Silicone Water Bottle 500ml", "price_usd": 22.99, "weight": 85, "dims": (12, 8, 6)},
            {"title": "Collapsible Water Bottle Hiking 750ml", "price_usd": 28.99, "weight": 110, "dims": (14, 9, 7)},
        ],
        "pet hair remover glove": [
            {"title": "Pet Hair Remover Glove Double Sided", "price_usd": 15.99, "weight": 65, "dims": (25, 15, 2)},
            {"title": "Cat Dog Grooming Glove Washable", "price_usd": 19.99, "weight": 72, "dims": (26, 16, 2)},
        ],
    }

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        await asyncio.sleep(0.1)

        products = self.MOCK_PRODUCTS.get(niche.lower(), [])
        results = []

        for i, p in enumerate(products[:limit]):
            results.append(RawProductData(
                title=p["title"],
                niche=niche,
                source="mock",
                source_url=f"https://example.com/mock/{niche}_{i}",
                wholesale_price_cny=round(p["price_usd"] / 4 * 7.2, 2),
                weight_g=p["weight"],
                dimensions_cm=p["dims"],
                supplier_rating=4.8,
                refund_rate_pct=1.0,
                has_actual_photos=True,
                supplier_contact="MOCK-SUPPLIER",
                longtail_keywords=[
                    {"keyword": f"{niche} for home", "volume": 1500, "kd": 20, "volume_provenance": "MOCK", "kd_provenance": "MOCK"},
                    {"keyword": f"{niche} for office", "volume": 1200, "kd": 22, "volume_provenance": "MOCK", "kd_provenance": "MOCK"},
                    {"keyword": f"{niche} for travel", "volume": 800, "kd": 25, "volume_provenance": "MOCK", "kd_provenance": "MOCK"},
                ],
                google_trends_yoy_pct=25.0,
                competitor_sales_90d=300,
                competitor_reviews=500,
                competitor_urls=[f"https://amazon.com/dp/MOCK{i}"],
                is_evergreen=True,
                estimated_aov_usd=p["price_usd"],
            ))

        return results


class SixteenEightEightFetcher(BaseFetcher):
    """1688 fetcher (需要登录, 默认不可用 —— 用户明确要求零凭据)"""

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        print("[1688] 已禁用: 需要登录凭据, 按用户要求走公开渠道。")
        return []


class TikTokFetcher(BaseFetcher):
    """TikTok Creative Center fetcher (无免费公开 API)"""

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        print("[TikTok] 已禁用: 无免费公开数据源。")
        return []


class CompetitorFetcher(BaseFetcher):
    """Competitor Shopify store monitor (to be implemented)"""

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        # TODO: Implement competitor product extraction
        return []


class PublicFetcherWrapper(BaseFetcher):
    """Wrapper for the public fetcher (纯公开真实数据)"""

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        return await fetch_niche_public(niche, limit)


class RealDataFetcher(BaseFetcher):
    """真实数据源聚合: Amazon 公开页(搜索+详情) + Google Suggest/Trends + 可选批发/Keepa。

    数据真实度:
    - Amazon 标题/价格/评分/评论数/BSR/重量/尺寸/bought-in-past-month: REAL
    - 销量估算: bought-in-past-month×3(基于真实徽章) > BSR 估算表 > 0
    - 批发价: 真实批发报价(如有) > CostEstimator 反推(ESTIMATED)
    - 绝不使用 random 生成数据
    """

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        # 1. 主数据源: 公开真实数据(Amazon + Google)
        public_task = fetch_niche_public(niche, limit)

        # 2. 可选: 批发报价(默认禁用, 反爬; 由 config.wholesale 控制)
        wholesale_task = fetch_wholesale_offers(niche, limit)

        public_data, wholesale_offers = await asyncio.gather(
            public_task, wholesale_task, return_exceptions=True
        )

        public_data = public_data if isinstance(public_data, list) else []
        wholesale_offers = wholesale_offers if isinstance(wholesale_offers, list) else []

        # 3. 批发报价按标题匹配合并(有真实报价才用, 否则留 0 由 CostEstimator 反推)
        wholesale_map = {self._normalize_title(o.title): o for o in wholesale_offers}

        for raw in public_data:
            comp_title_norm = self._normalize_title(raw.title)
            wholesale = wholesale_map.get(comp_title_norm)
            if wholesale:
                raw.wholesale_price_cny = round(
                    ((wholesale.price_usd_min + wholesale.price_usd_max) / 2) * 7.2, 2
                )
                raw.supplier_rating = wholesale.supplier_rating
                raw.supplier_contact = wholesale.supplier_name
                raw.has_actual_photos = bool(wholesale.image_url)

        return public_data

    @staticmethod
    def _normalize_title(title: str) -> str:
        import re
        title = title.lower()
        title = re.sub(r'[^\w\s]', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip()
        return title


async def fetch_niche(niche: str, source: str = "public", limit: int = 20) -> List[ProductCandidate]:
    """Fetch raw data and convert to ProductCandidate"""
    if source == "public":
        raw_products = await fetch_niche_public(niche, limit)
    elif source == "real":
        fetcher = RealDataFetcher()
        raw_products = await fetcher.fetch(niche, limit)
    else:
        fetcher = get_fetcher(source)
        raw_products = await fetcher.fetch(niche, limit)

    candidates = []
    cost_estimator = CostEstimator()

    for raw in raw_products:
        # 批发价: 真实报价 > CostEstimator 反推(基于真实竞品价, ESTIMATED)
        wholesale_price_usd = 0.0
        if raw.wholesale_price_cny > 0:
            wholesale_price_usd = raw.wholesale_price_cny / 7.2
        elif raw.estimated_aov_usd > 0:
            est = cost_estimator.estimate(
                competitor_retail_price_usd=raw.estimated_aov_usd,
                competitor_source=raw.source,
                weight_g=raw.weight_g or 200,
            )
            wholesale_price_usd = (est.estimated_wholesale_usd_min + est.estimated_wholesale_usd_max) / 2
            raw.cost_estimate = est

        candidate = ProductCandidate(
            title=raw.title,
            niche=raw.niche,
            pain_point_keywords=[kw["keyword"] for kw in (raw.longtail_keywords or [])[:5]],
            trend_score=raw.google_trends_yoy_pct / 100.0 if raw.google_trends_yoy_pct else 0.0,
            longtail_keywords=raw.longtail_keywords or [],
            google_trends_yoy_pct=raw.google_trends_yoy_pct,
            tiktok_hashtag_growth_pct=raw.tiktok_hashtag_growth_pct,
            amazon_bsr=raw.amazon_bsr,
            amazon_result_count=raw.amazon_result_count,
            wholesale_price_usd=round(wholesale_price_usd, 2),
            weight_g=raw.weight_g,
            dimensions_cm=raw.dimensions_cm,
            supplier_rating=raw.supplier_rating,
            refund_rate_pct=raw.refund_rate_pct,
            has_actual_photos=raw.has_actual_photos,
            supplier_url=raw.source_url,
            supplier_contact=raw.supplier_contact,
            competitor_urls=raw.competitor_urls or [],
            competitor_sales_90d=raw.competitor_sales_90d,
            competitor_reviews=raw.competitor_reviews,
            amazon_rating=raw.amazon_rating,
            market_proof_urls=raw.competitor_urls or [],
            is_evergreen=raw.is_evergreen,
            seasonal_peak_window_days=raw.seasonal_peak_window_days,
            prep_lead_time_days=raw.prep_lead_time_days,
            estimated_aov_usd=raw.estimated_aov_usd,
            estimated_repurchase_cycle_days=raw.estimated_repurchase_cycle_days,
            estimated_ltv_orders=raw.estimated_ltv_orders,
            source=raw.source,
        )
        candidates.append(candidate)

    # BSR 快照: 同 ASIN 同日覆盖, 跨日累积 → Gate2 单品趋势(BSR 改善=上升)
    for raw in raw_products:
        if raw.asin and raw.amazon_bsr > 0:
            record_bsr_snapshot(raw.asin, raw.amazon_bsr, raw.competitor_sales_90d)

    # Gate 6: Amazon 同款检测(REAL, 前3页; 只测前 max_check 个, 限速防反爬)
    try:
        await check_amazon_duplicates_batch(candidates, max_check=min(10, limit), concurrency=2)
    except Exception as e:
        print(f"[dup-check] batch failed: {e}")

    return candidates


def get_fetcher(source: str = "public") -> BaseFetcher:
    """Factory function to get fetcher by source name"""
    fetchers = {
        "mock": MockFetcher,
        "public": PublicFetcherWrapper,
        "real": RealDataFetcher,
        "1688": SixteenEightEightFetcher,
        "tiktok": TikTokFetcher,
        "competitor": CompetitorFetcher,
    }
    fetcher_class = fetchers.get(source, MockFetcher)
    return fetcher_class()
