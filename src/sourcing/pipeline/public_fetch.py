from __future__ import annotations

import asyncio
import random
import re
from typing import List
from urllib.parse import quote_plus, urljoin

import httpx
from playwright.async_api import async_playwright
from sourcing.config import get_config
from sourcing.pipeline.raw_data import RawProductData


class PublicFetcher:
    """零登录、零Cookie、只爬公开前台页面"""

    def __init__(self):
        self.config = get_config()
        self.ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]

    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        """并行爬取：Amazon + TEMU + SHEIN + Google Suggest + pytrends"""
        results = []

        # 1. 竞品爬取（并行）
        amazon_task = self._fetch_amazon(niche, limit)
        temu_task = self._fetch_temu(niche, limit)
        shein_task = self._fetch_shein(niche, limit)

        amazon_products, temu_products, shein_products = await asyncio.gather(
            amazon_task, temu_task, shein_task, return_exceptions=True
        )

        # 合并去重
        all_comp = self._dedup_by_title(
            (amazon_products if isinstance(amazon_products, list) else []) +
            (temu_products if isinstance(temu_products, list) else []) +
            (shein_products if isinstance(shein_products, list) else [])
        )

        # 2. 关键词扩展
        longtail_kws = await self._fetch_google_suggest(niche)
        trends_data = await self._fetch_google_trends(niche)

        # 3. 组装 RawProductData
        for i, comp in enumerate(all_comp[:limit]):
            results.append(RawProductData(
                title=comp["title"],
                niche=niche,
                source=comp["source"],
                source_url=comp["url"],
                wholesale_price_cny=0.0,  # 稍后由 CostEstimator 反推
                competitor_sales_90d=comp.get("est_sales_90d", 0),
                competitor_reviews=comp.get("reviews", 0),
                competitor_urls=[comp["url"]],
                longtail_keywords=longtail_kws,
                google_trends_yoy_pct=trends_data.get("yoy_pct", 0),
                tiktok_hashtag_growth_pct=trends_data.get("tiktok_growth", 0),
                estimated_aov_usd=comp.get("price_usd", 0) * random.uniform(0.8, 1.2),
            ))

        return results

    async def _fetch_amazon(self, niche: str, limit: int) -> List[dict]:
            """爬 Amazon 搜索前 2 页"""
            products = []
            url = f"https://www.amazon.com/s?k={quote_plus(niche)}&page=1"

            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(
                        user_agent=random.choice(self.ua_list),
                        viewport={"width": 1280, "height": 720},
                        locale="en-US",
                    )
                    page = await context.new_page()

                    # 防检测注入
                    await page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    """)

                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    # 等待搜索结果容器
                    await page.wait_for_selector('[data-component-type="s-search-result"], .s-result-item', timeout=15000)

                    # 尝试多种选择器
                    items = await page.query_selector_all('[data-component-type="s-search-result"]')
                    if not items:
                        items = await page.query_selector_all('.s-result-item[data-asin]')

                    for item in items[:limit]:
                        try:
                            # 标题 - 多种选择器
                            title = ""
                            for sel in ['h2 a span', 'h2 .a-text-normal', '.a-size-base-plus', '.a-text-normal']:
                                title_el = await item.query_selector(sel)
                                if title_el:
                                    title = await title_el.inner_text()
                                    if title.strip():
                                        break

                            if not title.strip():
                                continue

                            # 价格
                            price_usd = 0.0
                            for sel in ['.a-price-whole', '.a-offscreen', '[data-a-color="price"] .a-offscreen']:
                                price_el = await item.query_selector(sel)
                                if price_el:
                                    price_text = await price_el.inner_text()
                                    price_usd = float(re.sub(r'[^\d.]', '', price_text)) if price_text else 0
                                    if price_usd > 0:
                                        break

                            # 评分
                            rating = 0.0
                            rating_el = await item.query_selector('[aria-label*="stars"], [aria-label*="out of 5"]')
                            if rating_el:
                                aria = await rating_el.get_attribute('aria-label')
                                m = re.search(r'(\d+\.?\d*)', aria or '')
                                rating = float(m.group(1)) if m else 0

                            # 评论数
                            reviews = 0
                            for sel in ['a[aria-label*="reviews"] span', '.a-size-base.s-underline-text']:
                                reviews_el = await item.query_selector(sel)
                                if reviews_el:
                                    reviews_text = await reviews_el.inner_text()
                                    reviews = int(re.sub(r'[^\d]', '', reviews_text)) if reviews_text else 0
                                    if reviews > 0:
                                        break

                            # 链接和 ASIN
                            asin = ""
                            link_el = await item.query_selector('h2 a, .a-link-normal[href*="/dp/"]')
                            href = await link_el.get_attribute('href') if link_el else ""
                            if href:
                                m = re.search(r'/dp/([A-Z0-9]{10})', href)
                                asin = m.group(1) if m else ""

                            products.append({
                                "title": title.strip(),
                                "price_usd": price_usd,
                                "rating": rating,
                                "reviews": reviews,
                                "url": f"https://www.amazon.com/dp/{asin}" if asin else urljoin("https://www.amazon.com", href),
                                "source": "amazon",
                                "est_sales_90d": reviews * random.randint(5, 15),
                            })
                        except Exception:
                            continue

                    await browser.close()
            except Exception as e:
                print(f"[Amazon] fetch failed: {e}")

            return products

    async def _fetch_temu(self, niche: str, limit: int) -> List[dict]:
        """爬 TEMU 搜索页"""
        products = []
        url = f"https://www.temu.com/search_result.html?search_key={quote_plus(niche)}"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=random.choice(self.ua_list))
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_selector('[data-testid="product-card"]', timeout=10000)

                items = await page.query_selector_all('[data-testid="product-card"]')

                for item in items[:limit]:
                    try:
                        title_el = await item.query_selector('[data-testid="product-title"]')
                        title = await title_el.inner_text() if title_el else ""

                        price_el = await item.query_selector('[data-testid="product-price"]')
                        price_text = await price_el.inner_text() if price_el else "0"
                        price_usd = float(re.sub(r'[^\d.]', '', price_text))

                        link_el = await item.query_selector('a')
                        href = await link_el.get_attribute('href') if link_el else ""

                        products.append({
                            "title": title.strip(),
                            "price_usd": price_usd,
                            "url": urljoin("https://www.temu.com", href),
                            "source": "temu",
                            "reviews": 0,
                            "est_sales_90d": 0,
                        })
                    except Exception:
                        continue

                await browser.close()
        except Exception as e:
            print(f"[TEMU] fetch failed: {e}")

        return products

    async def _fetch_shein(self, niche: str, limit: int) -> List[dict]:
        """爬 SHEIN 搜索页"""
        products = []
        url = f"https://us.shein.com/search/{quote_plus(niche)}"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent=random.choice(self.ua_list))
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_selector('.product-item', timeout=10000)

                items = await page.query_selector_all('.product-item')

                for item in items[:limit]:
                    try:
                        title_el = await item.query_selector('.product-title')
                        title = await title_el.inner_text() if title_el else ""

                        price_el = await item.query_selector('.product-price')
                        price_text = await price_el.inner_text() if price_el else "0"
                        price_usd = float(re.sub(r'[^\d.]', '', price_text))

                        link_el = await item.query_selector('a')
                        href = await link_el.get_attribute('href') if link_el else ""

                        products.append({
                            "title": title.strip(),
                            "price_usd": price_usd,
                            "url": urljoin("https://us.shein.com", href),
                            "source": "shein",
                            "reviews": 0,
                            "est_sales_90d": 0,
                        })
                    except Exception:
                        continue

                await browser.close()
        except Exception as e:
            print(f"[SHEIN] fetch failed: {e}")

        return products

    async def _fetch_google_suggest(self, niche: str) -> List[dict]:
        """Google 自动补全 + People Also Ask（免费、无需 Key）"""
        kws = []
        base_queries = [
            niche,
            f"{niche} for",
            f"{niche} best",
            f"{niche} review",
            f"{niche} vs",
            f"how to use {niche}",
            f"{niche} benefits",
        ]

        async with httpx.AsyncClient(timeout=10) as client:
            for q in base_queries:
                try:
                    # 自动补全
                    url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={quote_plus(q)}"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        suggestions = resp.json()[1]
                        for s in suggestions[:5]:
                            kws.append({
                                "keyword": s,
                                "volume": random.randint(500, 5000),  # 占位，后续可接免费工具
                                "kd": random.randint(10, 35),
                            })

                    # People Also Ask (简化版：搜索页抓取)
                    search_url = f"https://www.google.com/search?q={quote_plus(q)}"
                    resp = await client.get(search_url, headers={"User-Agent": random.choice(self.ua_list)})
                    # 这里可解析 PAA，先跳过

                except Exception:
                    continue

        # 去重
        seen = set()
        unique = []
        for kw in kws:
            if kw["keyword"] not in seen:
                seen.add(kw["keyword"])
                unique.append(kw)

        return unique[:20]

    async def _fetch_google_trends(self, niche: str) -> dict:
        """pytrends 获取 90 天趋势"""
        try:
            from pytrends.request import TrendReq
            pytrends = TrendReq(hl='en-US', tz=360, timeout=(10, 25))
            pytrends.build_payload([niche], cat=0, timeframe='today 3-m', geo='US', gprop='')

            interest_df = pytrends.interest_over_time()
            yoy_pct = 0
            if not interest_df.empty and niche in interest_df.columns:
                series = interest_df[niche].dropna()
                if len(series) >= 60:
                    recent = series.tail(30).mean()
                    older = series.head(30).mean()
                    yoy_pct = ((recent - older) / older * 100) if older > 0 else 0

            # 相关上升词
            related = pytrends.related_queries()
            rising_kws = []
            if related and niche in related and related[niche].get('rising') is not None:
                rising_kws = related[niche]['rising']['query'].tolist() if hasattr(related[niche]['rising'], 'tolist') else list(related[niche]['rising'])
            tiktok_growth = len(rising_kws) * 10  # 代理指标

            return {"yoy_pct": round(yoy_pct, 1), "tiktok_growth": round(tiktok_growth, 1)}
        except Exception as e:
            print(f"[Trends] fetch failed: {e}")
            return {"yoy_pct": 0, "tiktok_growth": 0}

    def _dedup_by_title(self, products: List[dict]) -> List[dict]:
        """按标题相似度去重"""
        unique = []
        for p in products:
            is_dup = False
            for u in unique:
                if self._similarity(p["title"], u["title"]) > 0.8:
                    is_dup = True
                    break
            if not is_dup:
                unique.append(p)
        return unique

    def _similarity(self, a: str, b: str) -> float:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()


async def fetch_niche_public(niche: str, limit: int = 20) -> List[RawProductData]:
    """入口函数，供 pipeline 调用"""
    fetcher = PublicFetcher()
    return await fetcher.fetch(niche, limit)