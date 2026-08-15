from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import List, Optional
from sourcing.models import ProductCandidate
from sourcing.config import get_config
from sourcing.database import upsert_candidate
from sourcing.pipeline.cost_estimator import CostEstimator, enrich_with_cost_estimate
from sourcing.pipeline.wholesale_fetch import fetch_wholesale_offers, WholesaleOffer
from sourcing.pipeline.keepa_api import enrich_with_keepa, KeepaProduct
from sourcing.pipeline.public_fetch import fetch_niche_public, PublicFetcher


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
    # Competitor data
    competitor_sales_90d: int = 0
    competitor_reviews: int = 0
    competitor_urls: List[str] = field(default_factory=list)
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


class BaseFetcher:
    """Base class for all data fetchers"""
    
    def __init__(self):
        self.config = get_config()
    
    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        raise NotImplementedError


class MockFetcher(BaseFetcher):
    """Mock fetcher for testing - replaces real APIs during development"""
    
    MOCK_PRODUCTS = {
        "portable neck massager": [
            {"title": "便携颈部按摩器 无线热敷 EMS", "price_cny": 45, "weight": 180, "dims": (15, 10, 5)},
            {"title": "颈椎按摩仪 家用办公 多模式", "price_cny": 68, "weight": 220, "dims": (18, 12, 6)},
        ],
        "magnetic cable organizer": [
            {"title": "磁吸数据线收纳器 桌面整理", "price_cny": 12, "weight": 35, "dims": (8, 5, 2)},
            {"title": "强磁线缆管理夹 多槽位", "price_cny": 18, "weight": 42, "dims": (10, 6, 3)},
        ],
        "foldable silicone water bottle": [
            {"title": "折叠硅胶水杯 户外便携 500ml", "price_cny": 22, "weight": 85, "dims": (12, 8, 6)},
            {"title": "硅胶折叠水壶 登山露营 750ml", "price_cny": 28, "weight": 110, "dims": (14, 9, 7)},
        ],
        "pet hair remover glove": [
            {"title": "宠物除毛手套 双面硅胶 按摩", "price_cny": 15, "weight": 65, "dims": (25, 15, 2)},
            {"title": "猫狗除毛手套 可水洗 静电吸附", "price_cny": 19, "weight": 72, "dims": (26, 16, 2)},
        ],
    }
    
    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        await asyncio.sleep(0.1)  # simulate network
        
        products = self.MOCK_PRODUCTS.get(niche.lower(), [])
        results = []
        
        for i, p in enumerate(products[:limit]):
            # Generate mock keyword data
            keywords = [
                {"keyword": f"{niche} for {kw}", "volume": random.randint(500, 5000), "kd": random.randint(10, 35)}
                for kw in ["home", "office", "travel", "gift", "relief"]
            ]
            
            results.append(RawProductData(
                title=p["title"],
                niche=niche,
                source="1688_mock",
                source_url=f"https://1688.com/item/mock_{niche}_{i}.html",
                wholesale_price_cny=p["price_cny"],
                weight_g=p["weight"],
                dimensions_cm=p["dims"],
                supplier_rating=round(random.uniform(4.5, 4.9), 1),
                refund_rate_pct=round(random.uniform(0.5, 2.5), 1),
                has_actual_photos=True,
                supplier_contact=f"供应商_{niche}_{i}",
                longtail_keywords=keywords,
                google_trends_yoy_pct=round(random.uniform(15, 45), 1),
                tiktok_hashtag_growth_pct=round(random.uniform(30, 80), 1),
                competitor_sales_90d=random.randint(60, 500),
                competitor_reviews=random.randint(200, 2000),
                competitor_urls=[f"https://amazon.com/dp/MOCK{i}", f"https://shopify-competitor.com/products/mock-{i}"],
                is_evergreen=True,
                estimated_aov_usd=round(random.uniform(55, 95), 2),
                estimated_repurchase_cycle_days=random.randint(60, 120),
                estimated_ltv_orders=round(random.uniform(2.0, 4.5), 1),
            ))
        
        return results


class SixteenEightEightFetcher(BaseFetcher):
    """1688 fetcher using Playwright (to be implemented)"""
    
    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        # TODO: Implement with Playwright
        # 1. Search 1688 for niche keywords
        # 2. Extract product list
        # 3. For each product, visit detail page for specs, supplier info
        # 4. Return RawProductData list
        return []


class TikTokFetcher(BaseFetcher):
    """TikTok Creative Center fetcher (to be implemented)"""
    
    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        # TODO: Implement TikTok trending product scraping
        return []


class CompetitorFetcher(BaseFetcher):
    """Competitor Shopify store monitor (to be implemented)"""
    
    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        # TODO: Implement competitor product extraction
        return []


class PublicFetcherWrapper(BaseFetcher):
    """Wrapper for the public fetcher"""

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        return await fetch_niche_public(niche, limit)


class RealDataFetcher(BaseFetcher):
    """真实数据源聚合爬虫：Amazon + AliExpress/Alibaba + Google Trends + Keepa"""

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        """并行获取所有真实数据源"""
        
        # 1. 并行获取：竞品数据 + 批发报价 + 趋势/关键词
        amazon_task = self._fetch_amazon(niche, limit)
        wholesale_task = fetch_wholesale_offers(niche, limit)
        public_task = fetch_niche_public(niche, limit)  # 包含 Google Trends + Suggest + PAA

        amazon_products, wholesale_offers, public_data = await asyncio.gather(
            amazon_task, wholesale_task, public_task, return_exceptions=True
        )

        # 处理异常
        amazon_products = amazon_products if isinstance(amazon_products, list) else []
        wholesale_offers = wholesale_offers if isinstance(wholesale_offers, list) else []
        public_data = public_data if isinstance(public_data, list) else []

        # 2. 提取 ASIN 用于 Keepa 查询
        asins = [p.get("asin", "") for p in amazon_products if p.get("asin")]
        keepa_data = {}
        if asins:
            keepa_data = await enrich_with_keepa(asins)

        # 3. 合并数据：以 Amazon 竞品为主，补充批发价、趋势、关键词
        results = []
        
        # 构建批发价查找表（按标题匹配）
        wholesale_map = {self._normalize_title(o.title): o for o in wholesale_offers}
        
        for comp in amazon_products[:limit]:
            asin = comp.get("asin", "")
            keepa = keepa_data.get(asin)
            
            # 匹配批发报价
            comp_title_norm = self._normalize_title(comp.get("title", ""))
            wholesale = wholesale_map.get(comp_title_norm)
            
            # 如果没匹配到，尝试模糊匹配
            if not wholesale and wholesale_offers:
                for w in wholesale_offers:
                    if self._similarity(comp_title_norm, self._normalize_title(w.title)) > 0.6:
                        wholesale = w
                        break
            
            # 估算批发价：优先用真实批发报价，其次用 Keepa 数据，最后用反推
            wholesale_price_usd = 0.0
            if wholesale:
                wholesale_price_usd = (wholesale.price_usd_min + wholesale.price_usd_max) / 2
            elif keepa and keepa.current_price > 0:
                # 用 Keepa 当前价格反推批发价（假设 3.5-5x 倍率）
                wholesale_price_usd = keepa.current_price / 4.0
            
            # 竞品销量：优先 Keepa 估算，其次评论数代理
            competitor_sales_90d = 0
            if keepa and keepa.estimated_monthly_sales > 0:
                competitor_sales_90d = keepa.estimated_monthly_sales * 3
            else:
                competitor_sales_90d = comp.get("reviews", 0) * random.randint(5, 15)
            
            # 关键词数据：合并 public fetcher 的 Google Suggest + PAA
            longtail_kws = []
            for pd in public_data:
                longtail_kws.extend(pd.longtail_keywords)
            
            # 去重关键词
            seen_kws = set()
            unique_kws = []
            for kw in longtail_kws:
                if kw["keyword"] not in seen_kws:
                    seen_kws.add(kw["keyword"])
                    unique_kws.append(kw)
            
            # 趋势数据
            trends_yoy = 0.0
            tiktok_growth = 0.0
            for pd in public_data:
                trends_yoy = max(trends_yoy, pd.google_trends_yoy_pct)
                tiktok_growth = max(tiktok_growth, pd.tiktok_hashtag_growth_pct)
            
            # 重量尺寸估算（从标题解析或默认）
            weight_g, dims = self._estimate_physical(comp.get("title", ""))
            
            results.append(RawProductData(
                title=comp.get("title", ""),
                niche=niche,
                source="amazon_real",
                source_url=comp.get("url", ""),
                wholesale_price_cny=wholesale_price_usd * 7.2 if wholesale_price_usd > 0 else 0,
                weight_g=weight_g,
                dimensions_cm=dims,
                supplier_rating=wholesale.supplier_rating if wholesale else 0.0,
                refund_rate_pct=0.0,
                has_actual_photos=bool(wholesale and wholesale.image_url),
                supplier_contact=wholesale.supplier_name if wholesale else "",
                longtail_keywords=unique_kws[:30],
                google_trends_yoy_pct=trends_yoy,
                tiktok_hashtag_growth_pct=tiktok_growth,
                competitor_sales_90d=competitor_sales_90d,
                competitor_reviews=comp.get("reviews", 0),
                competitor_urls=[comp.get("url", "")] if comp.get("url") else [],
                is_evergreen=True,
                estimated_aov_usd=keepa.current_price if keepa else comp.get("price_usd", 0),
            ))

        return results

    async def _fetch_amazon(self, niche: str, limit: int) -> List[dict]:
        """复用 PublicFetcher 的 Amazon 爬虫逻辑"""
        pf = PublicFetcher()
        return await pf._fetch_amazon(niche, limit)

    def _normalize_title(self, title: str) -> str:
        """标准化标题用于匹配"""
        import re
        title = title.lower()
        title = re.sub(r'[^\w\s]', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip()
        return title

    def _similarity(self, a: str, b: str) -> float:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a, b).ratio()

    def _estimate_physical(self, title: str) -> tuple[float, tuple[float, float, float]]:
        """从标题估算重量和尺寸"""
        title_lower = title.lower()
        if any(kw in title_lower for kw in ["massager", "按摩", "neck", "颈"]):
            return 180.0, (15.0, 10.0, 5.0)
        elif any(kw in title_lower for kw in ["cable", "organizer", "线", "磁吸", "数据线"]):
            return 35.0, (8.0, 5.0, 2.0)
        elif any(kw in title_lower for kw in ["bottle", "cup", "水杯", "水壶", "硅胶"]):
            return 85.0, (12.0, 8.0, 6.0)
        elif any(kw in title_lower for kw in ["glove", "手套", "pet", "宠物", "除毛"]):
            return 65.0, (25.0, 15.0, 2.0)
        return 200.0, (15.0, 10.0, 5.0)


async def fetch_niche(niche: str, source: str = "mock", limit: int = 20) -> List[ProductCandidate]:
    """Fetch raw data and convert to ProductCandidate"""
    
    # Import here to avoid circular import
    if source == "public":
        from sourcing.pipeline.public_fetch import fetch_niche_public
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
        # For public fetcher, wholesale_price_cny is 0, need to estimate from competitor retail price
        wholesale_price_usd = 0.0
        if raw.wholesale_price_cny > 0:
            wholesale_price_usd = raw.wholesale_price_cny / 7.2
        elif raw.competitor_urls and hasattr(raw, 'estimated_aov_usd') and raw.estimated_aov_usd > 0:
            # Use competitor price to estimate wholesale
            # The public fetcher puts competitor price in estimated_aov_usd temporarily
            est = cost_estimator.estimate(
                competitor_retail_price_usd=raw.estimated_aov_usd,
                competitor_source=raw.source,
                weight_g=raw.weight_g or 200,
            )
            wholesale_price_usd = (est.estimated_wholesale_usd_min + est.estimated_wholesale_usd_max) / 2
            # Store cost estimate for later use
            raw.cost_estimate = est
        
        candidate = ProductCandidate(
            title=raw.title,
            niche=raw.niche,
            pain_point_keywords=[kw["keyword"] for kw in (raw.longtail_keywords or [])[:5]],
            trend_score=raw.google_trends_yoy_pct / 100.0,
            longtail_keywords=raw.longtail_keywords or [],
            google_trends_yoy_pct=raw.google_trends_yoy_pct,
            tiktok_hashtag_growth_pct=raw.tiktok_hashtag_growth_pct,
            wholesale_price_usd=wholesale_price_usd,
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
    
    return candidates


def get_fetcher(source: str = "mock") -> BaseFetcher:
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