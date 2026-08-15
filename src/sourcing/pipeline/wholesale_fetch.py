from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus, urljoin

import httpx
from playwright.async_api import async_playwright
from sourcing.config import get_config


@dataclass
class WholesaleOffer:
    """真实批发报价"""
    title: str
    price_usd_min: float
    price_usd_max: float
    moq: int
    supplier_name: str
    supplier_url: str
    supplier_country: str
    supplier_rating: float
    supplier_response_rate: str
    supplier_response_time: str
    product_url: str
    image_url: str
    specs: dict = field(default_factory=dict)


class AliExpressFetcher:
    """AliExpress/Alibaba 国际站公开搜索爬虫（免登录）"""

    def __init__(self):
        self.config = get_config()
        self.ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]

    async def search_wholesale(self, niche: str, limit: int = 10) -> List[WholesaleOffer]:
        """搜索批发报价"""
        offers = []

        # 并行爬 AliExpress 和 Alibaba
        aliexpress_task = self._fetch_aliexpress(niche, limit)
        alibaba_task = self._fetch_alibaba(niche, limit)

        aliexpress_offers, alibaba_offers = await asyncio.gather(
            aliexpress_task, alibaba_task, return_exceptions=True
        )

        if isinstance(aliexpress_offers, list):
            offers.extend(aliexpress_offers)
        if isinstance(alibaba_offers, list):
            offers.extend(alibaba_offers)

        # 去重（按标题相似度）
        return self._dedup_offers(offers)[:limit]

    async def _fetch_aliexpress(self, niche: str, limit: int) -> List[WholesaleOffer]:
        """爬 AliExpress 批发搜索页"""
        offers = []
        url = f"https://www.aliexpress.com/wholesale?SearchText={quote_plus(niche)}&catId=0&initiative_id=SB_20240101000000"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=random.choice(self.ua_list),
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                page = await context.new_page()

                # 防检测
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
                """)

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_selector('.list--gallery--34TropR, .list--galleryWrapper--29HRJT4, [data-ae_object_type="product"]', timeout=20000)

                # 处理可能的弹窗
                try:
                    await page.click('[aria-label="Close"], .btn-close, .close-btn', timeout=3000)
                except:
                    pass

                items = await page.query_selector_all('.list--gallery--34TropR > div, [data-ae_object_type="product"], .product-card, .manhattan-product-card')

                for item in items[:limit]:
                    try:
                        offer = await self._parse_aliexpress_item(item)
                        if offer:
                            offers.append(offer)
                    except Exception as e:
                        continue

                await browser.close()
        except Exception as e:
            print(f"[AliExpress] fetch failed: {e}")

        return offers

    async def _parse_aliexpress_item(self, item) -> Optional[WholesaleOffer]:
        """解析 AliExpress 单个商品卡片"""
        try:
            # 标题
            title = ""
            for sel in ['h3 a', '.product-title', '[title]', '.multi--titleText--nXeOvyr']:
                el = await item.query_selector(sel)
                if el:
                    title = await el.get_attribute('title') or await el.inner_text()
                    if title.strip():
                        break

            if not title.strip():
                return None

            # 价格区间
            price_min = 0.0
            price_max = 0.0
            for sel in ['.multi--price-sale--U-S0jt8', '.price--salePrice--1fzq5', '.price--originalPrice--2v4Yx']:
                el = await item.query_selector(sel)
                if el:
                    price_text = await el.inner_text()
                    prices = re.findall(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                    if prices:
                        price_min = float(prices[0])
                        price_max = float(prices[-1]) if len(prices) > 1 else price_min
                        break

            # MOQ
            moq = 1
            for sel in ['.trade--moq--', '.min-order', '[data-moq]']:
                el = await item.query_selector(sel)
                if el:
                    moq_text = await el.inner_text()
                    m = re.search(r'(\d+)', moq_text)
                    if m:
                        moq = int(m.group(1))
                        break

            # 供应商信息
            supplier_name = ""
            supplier_url = ""
            supplier_country = "China"
            supplier_rating = 0.0
            supplier_response_rate = ""
            supplier_response_time = ""

            for sel in ['.store-info a', '.shop-name a', '[data-store]']:
                el = await item.query_selector(sel)
                if el:
                    supplier_name = await el.inner_text()
                    supplier_url = await el.get_attribute('href') or ""
                    break

            # 评分
            for sel in ['.rating', '.score', '[data-rating]']:
                el = await item.query_selector(sel)
                if el:
                    rating_text = await el.inner_text()
                    m = re.search(r'(\d+\.?\d*)', rating_text)
                    if m:
                        supplier_rating = float(m.group(1))
                        break

            # 商品链接
            product_url = ""
            for sel in ['h3 a', '.product-title a', 'a[href*="/item/"]']:
                el = await item.query_selector(sel)
                if el:
                    product_url = await el.get_attribute('href') or ""
                    break

            if product_url and not product_url.startswith('http'):
                product_url = urljoin("https://www.aliexpress.com", product_url)

            # 图片
            image_url = ""
            img_el = await item.query_selector('img')
            if img_el:
                image_url = await img_el.get_attribute('src') or await img_el.get_attribute('data-src') or ""

            return WholesaleOffer(
                title=title.strip(),
                price_usd_min=price_min,
                price_usd_max=price_max,
                moq=moq,
                supplier_name=supplier_name.strip(),
                supplier_url=supplier_url,
                supplier_country=supplier_country,
                supplier_rating=supplier_rating,
                supplier_response_rate=supplier_response_rate,
                supplier_response_time=supplier_response_time,
                product_url=product_url,
                image_url=image_url,
            )
        except Exception:
            return None

    async def _fetch_alibaba(self, niche: str, limit: int) -> List[WholesaleOffer]:
        """爬 Alibaba 国际站搜索页"""
        offers = []
        url = f"https://www.alibaba.com/trade/search?fsb=y&IndexArea=product_en&CatId=&SearchText={quote_plus(niche)}&viewtype=G"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=random.choice(self.ua_list),
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                page = await context.new_page()

                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                """)

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_selector('.organic-gallery-offer-outter, .offer-card, .m-gallery-product-item-v2', timeout=20000)

                items = await page.query_selector_all('.organic-gallery-offer-outter, .offer-card, .m-gallery-product-item-v2')

                for item in items[:limit]:
                    try:
                        offer = await self._parse_alibaba_item(item)
                        if offer:
                            offers.append(offer)
                    except Exception:
                        continue

                await browser.close()
        except Exception as e:
            print(f"[Alibaba] fetch failed: {e}")

        return offers

    async def _parse_alibaba_item(self, item) -> Optional[WholesaleOffer]:
        """解析 Alibaba 单个商品卡片"""
        try:
            title = ""
            for sel in ['.offer-title a', '.elements-title-normal__outter a', 'h2 a', '[title]']:
                el = await item.query_selector(sel)
                if el:
                    title = await el.get_attribute('title') or await el.inner_text()
                    if title.strip():
                        break

            if not title.strip():
                return None

            # 价格
            price_min = 0.0
            price_max = 0.0
            for sel in ['.price-original', '.price-sale', '.price-whole', '.gal-price']:
                el = await item.query_selector(sel)
                if el:
                    price_text = await el.inner_text()
                    prices = re.findall(r'[\d,]+\.?\d*', price_text.replace(',', ''))
                    if prices:
                        price_min = float(prices[0])
                        price_max = float(prices[-1]) if len(prices) > 1 else price_min
                        break

            # MOQ
            moq = 1
            for sel in ['.min-order', '.moq', '[data-moq]']:
                el = await item.query_selector(sel)
                if el:
                    moq_text = await el.inner_text()
                    m = re.search(r'(\d+)', moq_text)
                    if m:
                        moq = int(m.group(1))
                        break

            # 供应商
            supplier_name = ""
            supplier_url = ""
            supplier_rating = 0.0
            supplier_country = "China"

            for sel in ['.company-name a', '.supplier-name a', '.st-company-name a']:
                el = await item.query_selector(sel)
                if el:
                    supplier_name = await el.inner_text()
                    supplier_url = await el.get_attribute('href') or ""
                    break

            # 评分
            for sel in ['.score', '.rating', '[data-score]']:
                el = await item.query_selector(sel)
                if el:
                    rating_text = await el.inner_text()
                    m = re.search(r'(\d+\.?\d*)', rating_text)
                    if m:
                        supplier_rating = float(m.group(1))
                        break

            # 商品链接
            product_url = ""
            for sel in ['.offer-title a', 'h2 a', 'a[href*=".html"]']:
                el = await item.query_selector(sel)
                if el:
                    product_url = await el.get_attribute('href') or ""
                    break

            if product_url and not product_url.startswith('http'):
                product_url = urljoin("https://www.alibaba.com", product_url)

            # 图片
            image_url = ""
            img_el = await item.query_selector('img')
            if img_el:
                image_url = await img_el.get_attribute('src') or await img_el.get_attribute('data-src') or ""

            return WholesaleOffer(
                title=title.strip(),
                price_usd_min=price_min,
                price_usd_max=price_max,
                moq=moq,
                supplier_name=supplier_name.strip(),
                supplier_url=supplier_url,
                supplier_country=supplier_country,
                supplier_rating=supplier_rating,
                supplier_response_rate="",
                supplier_response_time="",
                product_url=product_url,
                image_url=image_url,
            )
        except Exception:
            return None

    def _dedup_offers(self, offers: List[WholesaleOffer]) -> List[WholesaleOffer]:
        """按标题去重"""
        unique = []
        for o in offers:
            is_dup = False
            for u in unique:
                if self._similarity(o.title, u.title) > 0.85:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(o)
        return unique

    def _similarity(self, a: str, b: str) -> float:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()


async def fetch_wholesale_offers(niche: str, limit: int = 10) -> List[WholesaleOffer]:
    """入口函数"""
    fetcher = AliExpressFetcher()
    return await fetcher.search_wholesale(niche, limit)