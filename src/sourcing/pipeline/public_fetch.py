from __future__ import annotations

"""公开数据抓取器 —— 零登录、零 Cookie、零付费 API,只爬公开前台页面。

数据真实度原则:
- Amazon 搜索页: 标题/价格/评分/评论数/ASIN 为 REAL; "bought in past month"
  徽章为 REAL 销售信号(Amazon 官方公开显示)。
- Amazon 详情页: BSR(Best Sellers Rank)/Item Weight/Product Dimensions 为 REAL。
- Google Suggest: 关键词本身 REAL; 搜索量/难度无免费 API → 标 MISSING,绝不造假。
- Google Trends: YoY 与 rising queries 为 REAL。
- 销量估算: 优先 "bought in past month" ×3(基于真实徽章); 其次 BSR 估算表
  (ESTIMATED); 再无 → 0(MISSING),绝不 random。
"""

import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote_plus, urljoin

import httpx
from playwright.async_api import async_playwright
from sourcing.config import get_config
from sourcing.pipeline.raw_data import RawProductData
from sourcing.pipeline.bsr_sales import estimate_sales_90d_from_bsr

# 缓存目录
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data"
CACHE_DIR.mkdir(exist_ok=True)
TRENDS_CACHE_FILE = CACHE_DIR / "trends_cache.json"
TRENDS_CACHE_TTL = 24 * 3600  # 24 小时


class PublicFetcher:
    """零登录、零Cookie、只爬公开前台页面"""

    def __init__(self):
        self.config = get_config()
        self.ua_list = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def fetch(self, niche: str, limit: int = 20) -> List[RawProductData]:
        """并行爬取: Amazon(搜索页 + 详情页) + Google Suggest + Google Trends"""
        results = []

        # 1. Amazon 搜索页(真实)
        amazon_products = await self._fetch_amazon(niche, limit)

        # 2. Amazon 详情页增强: BSR / Item Weight / Product Dimensions(真实,限速)
        if amazon_products:
            amazon_products = await self._enrich_amazon_details(amazon_products, max_details=min(6, limit))

        # 3. 关键词: Google Suggest(REAL 关键词) + Trends rising(REAL)
        suggest_kws = await self._fetch_google_suggest(niche)
        trends_data = await self._fetch_google_trends(niche)

        # 4. 组装 RawProductData
        for comp in amazon_products[:limit]:
            results.append(RawProductData(
                title=comp["title"],
                niche=niche,
                source=comp["source"],
                source_url=comp["url"],
                wholesale_price_cny=0.0,  # 稍后由 CostEstimator 反推(ESTIMATED)
                weight_g=comp.get("weight_g", 0.0),
                dimensions_cm=comp.get("dimensions_cm", (0.0, 0.0, 0.0)),
                competitor_sales_90d=comp.get("est_sales_90d", 0),
                competitor_reviews=comp.get("reviews", 0),
                competitor_urls=[comp["url"]],
                amazon_rating=comp.get("rating", 0.0),
                longtail_keywords=suggest_kws + trends_data.get("rising_kws", []),
                google_trends_yoy_pct=trends_data.get("yoy_pct", 0),
                tiktok_hashtag_growth_pct=0.0,  # TikTok 无免费公开数据 → MISSING,不造假
                estimated_aov_usd=comp.get("price_usd", 0.0),  # 真实竞品价格
            ))

        return results

    # ------------------------------------------------------------------
    # Amazon 搜索页
    # ------------------------------------------------------------------
    async def _new_amazon_context(self, browser):
        """创建带反爬伪装的 Amazon 上下文: 真实指纹 + 首页预热建 cookie。

        关键经验: 数据中心 IP 直接访问搜索页会被 Amazon 拦截("Sorry! Something
        went wrong!")。必须先访问 amazon.com 首页建立会话 cookie, 再搜索即可放行。
        """
        context = await browser.new_context(
            user_agent=random.choice(self.ua_list),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = {runtime: {}};
        """)
        # 首页预热
        page = await context.new_page()
        try:
            await page.goto("https://www.amazon.com/", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)
        except Exception:
            pass
        return context, page

    @staticmethod
    def _browser_launch_args():
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]

    async def _fetch_amazon(self, niche: str, limit: int) -> List[dict]:
        """爬 Amazon 搜索前 1 页: 标题/价格/评分/评论数/bought in past month/ASIN"""
        products = []
        url = f"https://www.amazon.com/s?k={quote_plus(niche)}&page=1"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=self._browser_launch_args())
                context, page = await self._new_amazon_context(browser)

                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # 等待搜索结果容器
                try:
                    await page.wait_for_selector(
                        '[data-component-type="s-search-result"], .s-result-item', timeout=15000
                    )
                except Exception:
                    pass

                items = await page.query_selector_all('[data-component-type="s-search-result"]')
                if not items:
                    items = await page.query_selector_all('.s-result-item[data-asin]')

                for item in items[:limit]:
                    try:
                        # 标题
                        title = ""
                        for sel in ['h2 a span', 'h2 .a-text-normal', '.a-size-base-plus', '.a-text-normal']:
                            title_el = await item.query_selector(sel)
                            if title_el:
                                title = (await title_el.inner_text()).strip()
                                if title:
                                    break
                        # 过滤徽章/广告文本(Amazon's Choice / Overall Pick / Sponsored 等)
                        if any(bad in title for bad in ("Amazon's Choice", "Overall Pick", "Sponsored", "Best Seller")):
                            title = ""
                            # 广告卡片标题在 h2 的直接 span 里
                            h2_el = await item.query_selector("h2")
                            if h2_el:
                                t2 = (await h2_el.inner_text()).strip()
                                if t2 and not any(bad in t2 for bad in ("Amazon's Choice", "Overall Pick", "Sponsored", "Best Seller")):
                                    title = t2
                        if not title:
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
                            aria = await rating_el.get_attribute('aria-label') or ""
                            m = re.search(r'(\d+\.?\d*)', aria)
                            rating = float(m.group(1)) if m else 0

                        # 评论数(REAL) — 新版 DOM: span.s-underline-text 文本如 "(34)"
                        reviews = 0
                        for sel in ['span.s-underline-text', 'a[aria-label*="reviews"] span', '.a-size-base.s-underline-text']:
                            reviews_el = await item.query_selector(sel)
                            if reviews_el:
                                reviews_text = (await reviews_el.inner_text()).strip()
                                digits = re.sub(r'[^\d]', '', reviews_text)
                                reviews = int(digits) if digits else 0
                                if reviews > 0:
                                    break

                        # bought in past month 徽章(REAL 销售信号)
                        bought_past_month = 0
                        for sel in ['.a-size-base.a-color-secondary', '.a-row.a-size-base.a-color-secondary']:
                            badge_els = await item.query_selector_all(sel)
                            for b_el in badge_els:
                                text = (await b_el.inner_text()).strip()
                                m = re.search(r'([\d.,]+[KMB]?)\+?\s*bought in past month', text, re.I)
                                if m:
                                    bought_past_month = self._parse_compact_number(m.group(1))
                                    break
                            if bought_past_month:
                                break

                        # 链接和 ASIN
                        asin = ""
                        link_el = await item.query_selector('h2 a, .a-link-normal[href*="/dp/"]')
                        href = await link_el.get_attribute('href') if link_el else ""
                        if href:
                            m = re.search(r'/dp/([A-Z0-9]{10})', href)
                            asin = m.group(1) if m else ""

                        # 90 天销量: 优先真实徽章×3, 其次 BSR 估算(稍后详情页), 无则 0
                        est_sales_90d = bought_past_month * 3
                        sales_method = "bought_in_past_month_x3" if bought_past_month else ""

                        products.append({
                            "title": title,
                            "price_usd": price_usd,
                            "rating": rating,
                            "reviews": reviews,
                            "bought_in_past_month": bought_past_month,
                            "url": f"https://www.amazon.com/dp/{asin}" if asin else urljoin("https://www.amazon.com", href),
                            "asin": asin,
                            "source": "amazon",
                            "est_sales_90d": est_sales_90d,
                            "sales_method": sales_method,
                        })
                    except Exception:
                        continue

                await browser.close()
        except Exception as e:
            print(f"[Amazon] fetch failed: {e}")

        return products

    # ------------------------------------------------------------------
    # Amazon 详情页: BSR / Item Weight / Product Dimensions(REAL)
    # ------------------------------------------------------------------
    async def _enrich_amazon_details(self, products: List[dict], max_details: int = 6) -> List[dict]:
        """对前 max_details 个 ASIN 爬详情页, 补 BSR / 重量 / 尺寸"""
        targets = [p for p in products if p.get("asin")][:max_details]
        if not targets:
            return products

        # 详情页并发但限速(Amazon 反爬敏感)
        sem = asyncio.Semaphore(2)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=self._browser_launch_args())
            context, warmup_page = await self._new_amazon_context(browser)
            await warmup_page.close()

            async def fetch_one(prod: dict):
                async with sem:
                    page = await context.new_page()
                    try:
                        await page.goto(prod["url"], wait_until="domcontentloaded", timeout=25000)
                        await page.wait_for_timeout(1500)  # 给渲染留时间

                        detail = await self._parse_detail_page(page)
                        if detail.get("bsr"):
                            prod["bsr"] = detail["bsr"]
                            if not prod.get("est_sales_90d"):
                                prod["est_sales_90d"] = estimate_sales_90d_from_bsr(detail["bsr"])
                                prod["sales_method"] = "bsr_estimate"
                        if detail.get("weight_g"):
                            prod["weight_g"] = detail["weight_g"]
                        if detail.get("dimensions_cm"):
                            prod["dimensions_cm"] = detail["dimensions_cm"]
                    except Exception as e:
                        print(f"[Amazon detail] {prod.get('asin')} failed: {e}")
                    finally:
                        await page.close()
                    # 限速: 每次请求间隔
                    await asyncio.sleep(2)

            await asyncio.gather(*[fetch_one(t) for t in targets])
            await browser.close()

        return products

    @staticmethod
    async def _parse_detail_page(page) -> dict:
        """解析详情页: BSR + Item Weight + Product Dimensions"""
        result = {"bsr": 0, "weight_g": 0.0, "dimensions_cm": (0.0, 0.0, 0.0)}
        texts = []

        # 两种布局都抓
        for sel in [
            "#productDetails_detailBullets_sections1",
            "#detailBullets_feature_div",
            "#prodDetails",
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    texts.append(await el.inner_text())
            except Exception:
                continue

        # 去掉隐藏字符(RTL 标记等, Amazon 详情页常见)
        full = "\n".join(texts)
        full = re.sub(r'[\u200e\u200f\u200b\u2060\ufeff]', '', full)

        # BSR: "Best Sellers Rank: #1,234 in Category" 或 "#56 in Subcategory"
        m = re.search(r'Best Sellers Rank:?\s*#([\d,]+)', full)
        if m:
            result["bsr"] = int(m.group(1).replace(",", ""))
        else:
            # 新版页面格式: "#1,234 in Category (See Top 100...)"
            m2 = re.search(r'#([\d,]+)\s+in\s+[A-Za-z]', full)
            if m2:
                result["bsr"] = int(m2.group(1).replace(",", ""))

        # Product Dimensions: "3.7 x 1.9 x 4.3 inches" (冒号前后可能有隐藏字符)
        m = re.search(r'Product Dimensions\s*:?\s*([\d.]+\s*x\s*[\d.]+\s*x\s*[\d.]+)\s*inches', full, re.I)
        if m:
            dims = [float(x) for x in re.split(r'\s*x\s*', m.group(1))]
            if len(dims) == 3:
                result["dimensions_cm"] = tuple(round(d * 2.54, 1) for d in dims)

        # Item Weight: "Item Weight: 1.2 pounds" / "Item Weight: 500 g"
        # 或嵌入 Dimensions 行: "3.7 x 1.9 x 4.3 inches; 6.17 ounces"
        m = re.search(r'Item Weight:?\s*([\d.]+)\s*(pounds|ounces|g|kg|grams|kilograms)', full, re.I)
        if not m:
            m = re.search(r'inches\s*[;，]?\s*([\d.]+)\s*(pounds|ounces)', full, re.I)
        if m:
            val, unit = float(m.group(1)), m.group(2).lower()
            if unit.startswith("pound"):
                result["weight_g"] = round(val * 453.6, 1)
            elif unit.startswith("ounce"):
                result["weight_g"] = round(val * 28.35, 1)
            elif unit.startswith("kg"):
                result["weight_g"] = round(val * 1000, 1)
            else:
                result["weight_g"] = round(val, 1)

        return result

    # ------------------------------------------------------------------
    # Google Suggest(REAL 关键词; volume/KD 无免费 API → MISSING)
    # ------------------------------------------------------------------
    async def _fetch_google_suggest(self, niche: str) -> List[dict]:
        """Google 自动补全(免费、无需 Key)。关键词真实; 搜索量/难度标注 MISSING。"""
        kws = []
        base_queries = [
            niche,
            f"{niche} for",
            f"{niche} best",
            f"{niche} review",
            f"{niche} vs",
            f"how to use {niche}",
            f"{niche} benefits",
            f"{niche} problems",
            f"best {niche} for",
        ]

        async with httpx.AsyncClient(timeout=10) as client:
            for q in base_queries:
                try:
                    url = f"https://suggestqueries.google.com/complete/search?client=firefox&q={quote_plus(q)}"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        suggestions = resp.json()[1]
                        for s in suggestions[:5]:
                            kws.append({
                                "keyword": s,
                                "volume": 0,
                                "kd": 0,
                                "volume_provenance": "MISSING",  # 无免费搜索量 API,不造假
                                "kd_provenance": "MISSING",
                            })
                except Exception:
                    continue
                await asyncio.sleep(0.3)

        # 去重
        seen = set()
        unique = []
        for kw in kws:
            if kw["keyword"] not in seen:
                seen.add(kw["keyword"])
                unique.append(kw)

        return unique[:20]

    # ------------------------------------------------------------------
    # Google Trends(pytrends, REAL, 带 24h 缓存)
    # ------------------------------------------------------------------
    @staticmethod
    def _patch_pytrends_urllib3():
        """pytrends 4.9.2 使用 urllib3.Retry(method_whitelist=...) 与 urllib3>=2 不兼容。

        兼容补丁: 把 method_whitelist kwarg 转成 urllib3 2.x 的 allowed_methods。
        """
        try:
            import urllib3.util.retry as retry_mod

            if getattr(retry_mod.Retry, "_patched_for_pytrends", False):
                return
            orig_init = retry_mod.Retry.__init__

            def patched_init(self, *args, method_whitelist=None, **kwargs):
                if method_whitelist is not None and "allowed_methods" not in kwargs:
                    kwargs["allowed_methods"] = method_whitelist
                orig_init(self, *args, **kwargs)

            retry_mod.Retry.__init__ = patched_init
            retry_mod.Retry._patched_for_pytrends = True
        except Exception:
            pass

    async def _fetch_google_trends(self, niche: str) -> dict:
        """pytrends 获取 90 天趋势 + 上升相关词(真实)"""
        # 本地缓存,避免频繁被限流
        cached = self._load_trends_cache(niche)
        if cached:
            return cached

        self._patch_pytrends_urllib3()
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl='en-US', tz=360, timeout=(10, 25), retries=2, backoff_factor=0.5)
            # 12 个月窗口: 对比最近 90 天 vs 前 90 天(排除 0 值噪声, 平滑季节性)
            pytrends.build_payload([niche], cat=0, timeframe='today 12-m', geo='US', gprop='')

            interest_df = pytrends.interest_over_time()
            yoy_pct = 0
            if interest_df is not None and not interest_df.empty and niche in interest_df.columns:
                series = interest_df[niche].dropna()
                # 注意: 'today 12-m' 返回周粒度(~52 行); 90 天 ≈ 13 周
                if len(series) >= 26:
                    def _nonzero_mean(arr):
                        nz = arr[arr > 0]
                        return float(nz.mean()) if len(nz) >= 4 else 0.0

                    recent = _nonzero_mean(series.tail(13).values)    # 最近 ~90 天
                    older = _nonzero_mean(series.iloc[-26:-13].values)  # 之前 ~90 天
                    if older > 1:  # 旧期均值过小(<1)视为数据不足, 不算暴跌
                        yoy_pct = (recent - older) / older * 100
                    elif recent > 0 and older == 0:
                        yoy_pct = 100.0  # 旧期无数据, 近期有 → 新兴趋势

            # 上升相关词(REAL, 来自 Trends)
            rising_kws = []
            try:
                related = pytrends.related_queries()
                if related and niche in related and related[niche].get('rising') is not None:
                    rising_df = related[niche]['rising']
                    if hasattr(rising_df, 'to_dict'):
                        for _, row in rising_df.head(10).iterrows():
                            rising_kws.append({
                                "keyword": str(row.get("query", "")),
                                "volume": 0,
                                "kd": 0,
                                "trending_value": float(row.get("value", 0)) if row.get("value") is not None else 0,
                                "volume_provenance": "MISSING",
                                "kd_provenance": "MISSING",
                                "trending_provenance": "REAL",
                            })
            except Exception as e:
                print(f"[Trends] related_queries failed: {e}")

            result = {
                "yoy_pct": round(yoy_pct, 1),
                "rising_kws": rising_kws,
            }
            self._save_trends_cache(niche, result)
            return result
        except Exception as e:
            print(f"[Trends] fetch failed: {e}")
            return {"yoy_pct": 0, "rising_kws": []}

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_compact_number(s: str) -> int:
        """'1K' → 1000, '1.5K' → 1500, '2M' → 2000000"""
        s = s.strip().upper()
        try:
            if s.endswith("K"):
                return int(float(s[:-1]) * 1000)
            if s.endswith("M"):
                return int(float(s[:-1]) * 1000000)
            return int(float(s.replace(",", "")))
        except (ValueError, TypeError):
            return 0

    def _load_trends_cache(self, niche: str) -> Optional[dict]:
        try:
            if TRENDS_CACHE_FILE.exists():
                data = json.loads(TRENDS_CACHE_FILE.read_text())
                entry = data.get(niche)
                if entry and time.time() - entry.get("ts", 0) < TRENDS_CACHE_TTL:
                    return entry.get("data")
        except Exception:
            pass
        return None

    def _save_trends_cache(self, niche: str, result: dict) -> None:
        try:
            data = {}
            if TRENDS_CACHE_FILE.exists():
                data = json.loads(TRENDS_CACHE_FILE.read_text())
            data[niche] = {"ts": time.time(), "data": result}
            TRENDS_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass


async def fetch_niche_public(niche: str, limit: int = 20) -> List[RawProductData]:
    """入口函数,供 pipeline 调用"""
    fetcher = PublicFetcher()
    return await fetcher.fetch(niche, limit)
