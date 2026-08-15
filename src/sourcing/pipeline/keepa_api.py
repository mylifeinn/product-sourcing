from __future__ import annotations

import asyncio
import httpx
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from sourcing.config import get_config


@dataclass
class KeepaProduct:
    """Keepa 产品数据"""
    asin: str
    title: str
    brand: str
    category_tree: List[str]
    root_category: int
    
    # 价格历史
    current_price: float = 0.0
    lowest_price_30d: float = 0.0
    highest_price_30d: float = 0.0
    avg_price_90d: float = 0.0
    
    # BSR (Best Seller Rank)
    current_bsr: int = 0
    avg_bsr_30d: float = 0.0
    bsr_category: int = 0
    bsr_history: List[Dict] = field(default_factory=list)
    
    # 销量估算
    estimated_monthly_sales: int = 0
    estimated_daily_sales: float = 0.0
    sales_estimation_method: str = ""
    
    # 评分评论
    rating: float = 0.0
    review_count: int = 0
    review_velocity: float = 0.0  # 评论增长速度
    
    # Listing 质量
    images_count: int = 0
    has_video: bool = False
    bullet_points_count: int = 0
    description_length: int = 0
    
    # 变体
    variation_count: int = 0
    parent_asin: str = ""
    
    # FBA 费用估算
    fba_fees: Dict[str, float] = field(default_factory=dict)
    referral_fee_pct: float = 0.0
    
    # 原始数据
    raw_data: Dict = field(default_factory=dict)


class KeepaClient:
    """Keepa API 客户端（免费版：每天 100 个 token，每个请求消耗 1 token）"""
    
    BASE_URL = "https://api.keepa.com"
    
    def __init__(self, api_key: str = ""):
        self.api_key = api_key
        self.config = get_config()
        if not self.api_key:
            self.api_key = getattr(self.config, 'keepa_api_key', '')
    
    def _has_key(self) -> bool:
        return bool(self.api_key and self.api_key.strip())
    
    async def get_products(self, asins: List[str], domain: int = 1) -> List[KeepaProduct]:
        """批量查询产品（domain: 1=US, 2=UK, 3=DE, 4=FR, 5=JP, 6=CA, 7=CN, 8=IT, 9=ES, 10=IN, 11=MX, 12=BR, 13=AU）"""
        if not self._has_key():
            return []
        
        if not asins:
            return []
        
        # Keepa 单次最多 100 个 ASIN
        results = []
        for i in range(0, len(asins), 100):
            batch = asins[i:i+100]
            batch_results = await self._query_keepa(batch, domain)
            results.extend(batch_results)
            # 避免速率限制
            if i + 100 < len(asins):
                await asyncio.sleep(1)
        
        return results
    
    async def _query_keepa(self, asins: List[str], domain: int) -> List[KeepaProduct]:
        """实际调用 Keepa API"""
        params = {
            "key": self.api_key,
            "domain": domain,
            "asin": ",".join(asins),
            "stats": 90,  # 90 天统计
            "buybox": 1,
            "rating": 1,
            "history": 1,  # 价格历史
            "sales": 1,    # 销量估算
        }
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{self.BASE_URL}/product", params=params)
                if resp.status_code != 200:
                    print(f"[Keepa] API error: {resp.status_code} - {resp.text}")
                    return []
                
                data = resp.json()
                products = data.get("products", [])
                return [self._parse_product(p) for p in products]
        except Exception as e:
            print(f"[Keepa] query failed: {e}")
            return []
    
    def _parse_product(self, p: Dict) -> KeepaProduct:
        """解析 Keepa 产品数据"""
        # 基础信息
        asin = p.get("asin", "")
        title = p.get("title", "")
        brand = p.get("brand", "")
        category_tree = p.get("categoryTree", [])
        root_category = p.get("rootCategory", 0)
        
        # 价格数据 (Keepa 价格单位是 cents，需除以 100)
        csv = p.get("csv", [])
        current_price = 0.0
        if len(csv) > 1 and csv[1]:  # [0]=time, [1]=AMAZON price
            current_price = csv[1][-1] / 100 if csv[1] else 0.0
        
        # 统计数据
        stats = p.get("stats", {})
        avg_price_90d = stats.get("avg90", 0) / 100 if stats.get("avg90") else 0.0
        lowest_30d = stats.get("lowest30", 0) / 100 if stats.get("lowest30") else 0.0
        highest_30d = stats.get("highest30", 0) / 100 if stats.get("highest30") else 0.0
        
        # BSR
        current_bsr = stats.get("currentSalesRank", 0) or 0
        avg_bsr_30d = stats.get("avgSalesRank30", 0) or 0
        bsr_category = stats.get("salesRankCategory", 0) or 0
        
        # 销量估算
        sales_data = p.get("sales", {})
        estimated_monthly = sales_data.get("monthly", 0) or 0
        estimated_daily = sales_data.get("daily", 0.0) or 0.0
        method = sales_data.get("method", "")
        
        # 评分评论
        rating = p.get("rating", 0.0) or 0.0
        review_count = p.get("reviewCount", 0) or 0
        
        # 评论速度
        review_velocity = 0.0
        if "reviews" in p and p["reviews"]:
            recent_reviews = [r for r in p["reviews"] if r.get("date", 0) > 0]
            if recent_reviews:
                review_velocity = len(recent_reviews) / 30  # 最近30天日均
        
        # Listing 质量
        images_count = len(p.get("imagesCSV", [])) if p.get("imagesCSV") else 0
        has_video = bool(p.get("videoCount", 0))
        bullet_points_count = len(p.get("features", [])) if p.get("features") else 0
        description_length = len(p.get("description", "")) if p.get("description") else 0
        
        # 变体
        variation_count = len(p.get("variations", [])) if p.get("variations") else 0
        parent_asin = p.get("parentASIN", "")
        
        # FBA 费用
        fba_fees = {}
        if "fbaFees" in p:
            fba_fees = p["fbaFees"]
        
        referral_fee = p.get("referralFee", 0) / 100 if p.get("referralFee") else 0.0
        
        return KeepaProduct(
            asin=asin,
            title=title,
            brand=brand,
            category_tree=category_tree,
            root_category=root_category,
            current_price=current_price,
            lowest_price_30d=lowest_30d,
            highest_price_30d=highest_30d,
            avg_price_90d=avg_price_90d,
            current_bsr=current_bsr,
            avg_bsr_30d=avg_bsr_30d,
            bsr_category=bsr_category,
            estimated_monthly_sales=estimated_monthly,
            estimated_daily_sales=estimated_daily,
            sales_estimation_method=method,
            rating=rating,
            review_count=review_count,
            review_velocity=review_velocity,
            images_count=images_count,
            has_video=has_video,
            bullet_points_count=bullet_points_count,
            description_length=description_length,
            variation_count=variation_count,
            parent_asin=parent_asin,
            fba_fees=fba_fees,
            referral_fee_pct=referral_fee,
            raw_data=p,
        )
    
    async def search_products(self, keywords: str, domain: int = 1, max_results: int = 20) -> List[str]:
        """搜索关键词返回 ASIN 列表（需要 Product Finder 权限，免费版可能不可用）"""
        if not self._has_key():
            return []
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/query",
                    params={
                        "key": self.api_key,
                        "domain": domain,
                        "selection": keywords,
                        "maxResults": max_results,
                    }
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return [p.get("asin", "") for p in data.get("products", []) if p.get("asin")]
        except Exception as e:
            print(f"[Keepa] search failed: {e}")
        return []


# 单例客户端
_keepa_client: Optional[KeepaClient] = None


def get_keepa_client() -> Optional[KeepaClient]:
    global _keepa_client
    if _keepa_client is None:
        from sourcing.config import get_config
        config = get_config()
        api_key = config.keepa.api_key or ''
        if api_key:
            _keepa_client = KeepaClient(api_key)
    return _keepa_client if _keepa_client and _keepa_client._has_key() else None


async def enrich_with_keepa(asins: List[str]) -> Dict[str, KeepaProduct]:
    """批量用 Keepa 丰富产品数据"""
    client = get_keepa_client()
    if not client:
        return {}
    
    products = await client.get_products(asins)
    return {p.asin: p for p in products}