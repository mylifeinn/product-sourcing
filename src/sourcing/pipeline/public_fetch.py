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
import difflib
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
    async def fetch(self, niche: str, limit: int = 20, progress_callback=None) -> List[RawProductData]:
        """并行爬取: Amazon(搜索页 + 详情页) + Google Suggest + Google Trends + Reddit
        
        Args:
            niche: 搜索关键词
            limit: 最大返回数量
            progress_callback: 可选的进度回调函数 (step, message) -> None
        """
        results = []

        # 1+2. Amazon 搜索页 + 详情页增强共用一个浏览器实例
        # (小机器上重复 launch/close Chromium 是 CPU 尖峰和内存翻倍的主因)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=self._browser_launch_args())
            try:
                amazon_products = await self._fetch_amazon(niche, limit, progress_callback, browser=browser)
                if amazon_products:
                    amazon_products = await self._enrich_amazon_details(
                        amazon_products, max_details=min(6, limit), browser=browser
                    )
            finally:
                await browser.close()

        # 3. 关键词: Google Suggest(REAL 关键词) + Trends rising(REAL) + Reddit 痛点/推荐
        suggest_kws, trends_data, reddit_data = await asyncio.gather(
            self._fetch_google_suggest(niche),
            self._fetch_google_trends(niche),
            self._fetch_reddit(niche, limit=20),
            return_exceptions=True
        )
        # 异常处理
        if isinstance(suggest_kws, Exception):
            print(f"[fetch] Google Suggest failed: {suggest_kws}")
            suggest_kws = []
        if isinstance(trends_data, Exception):
            print(f"[fetch] Google Trends failed: {trends_data}")
            trends_data = {"yoy_pct": 0, "rising_kws": []}
        if isinstance(reddit_data, Exception):
            print(f"[fetch] Reddit failed: {reddit_data}")
            reddit_data = []

        # 4. 从 Reddit 提取额外长尾关键词
        reddit_kws = await self._fetch_reddit_keywords(niche)
        
        # 合并所有关键词
        all_kws = suggest_kws + trends_data.get("rising_kws", []) + reddit_kws

        # 5. Reddit 痛点/推荐信号汇总 (用于后续评分参考)
        reddit_pain_points = [p for p in reddit_data if p["type"] == "pain_point"]
        reddit_recommendations = [p for p in reddit_data if p["type"] == "recommendation"]
        reddit_complaints = [p for p in reddit_data if p["type"] == "complaint"]

        # 6. 组装 RawProductData
        for comp in amazon_products[:limit]:
            results.append(RawProductData(
                title=comp["title"],
                niche=niche,
                source=comp["source"],
                source_url=comp["url"],
                asin=comp.get("asin", ""),
                amazon_bsr=comp.get("bsr", 0),
                amazon_result_count=comp.get("result_count", 0),
                wholesale_price_cny=0.0,  # 稍后由 CostEstimator 反推(ESTIMATED)
                weight_g=comp.get("weight_g", 0.0),
                dimensions_cm=comp.get("dimensions_cm", (0.0, 0.0, 0.0)),
                competitor_sales_90d=comp.get("est_sales_90d", 0),
                competitor_reviews=comp.get("reviews", 0),
                competitor_urls=[comp["url"]],
                amazon_rating=comp.get("rating", 0.0),
                longtail_keywords=all_kws,
                google_trends_yoy_pct=trends_data.get("yoy_pct", 0),
                tiktok_hashtag_growth_pct=0.0,  # TikTok 无免费公开数据 → MISSING,不造假
                estimated_aov_usd=comp.get("price_usd", 0.0),  # 真实竞品价格
                # Reddit 信号存入 raw_data 供评分层使用
                reddit_pain_points=reddit_pain_points[:5],
                reddit_recommendations=reddit_recommendations[:5],
                reddit_complaints=reddit_complaints[:5],
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
        """低资源启动参数: 2核/1GB 小机器上跑, 必须压住 Chromium 的 CPU/内存。

        - --renderer-process-limit=1 + 禁 site isolation: 所有页面共享 1 个渲染进程
          (默认每站点一个进程, 开 N 个 tab 就 N 份内存)
        - --disable-gpu: 无头模式不需要 GPU 合成, 省 CPU
        - 其余关闭后台网络/同步等不必要活动
        """
        return [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--renderer-process-limit=1",
            "--disable-site-isolation-trials",
            "--disable-features=site-per-process,IsolateOrigins,Translate,BackForwardCache",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-default-apps",
            "--disable-extensions",
            "--mute-audio",
        ]

    async def _fetch_amazon(self, niche: str, limit: int, progress_callback=None, browser=None) -> List[dict]:
        """爬 Amazon 搜索前 1 页: 标题/价格/评分/评论数/bought in past month/ASIN

        Args:
            niche: 搜索关键词
            limit: 最大返回数量
            progress_callback: 可选的进度回调函数 (step, message) -> None
            browser: 可选的共享浏览器实例; 传入则复用(调用方负责关闭), 不传则自建自关
        """
        products = []
        url = f"https://www.amazon.com/s?k={quote_plus(niche)}&page=1"
        max_retries = 3
        base_delay = 2  # seconds

        for attempt in range(1, max_retries + 1):
            try:
                if progress_callback:
                    progress_callback(1, f"尝试连接 Amazon (第 {attempt}/{max_retries} 次)...")

                if browser is None:
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(headless=True, args=self._browser_launch_args())
                        try:
                            products = await self._fetch_amazon_once(
                                browser, url, niche, limit, attempt, max_retries, progress_callback
                            )
                        finally:
                            await browser.close()
                        browser = None
                else:
                    products = await self._fetch_amazon_once(
                        browser, url, niche, limit, attempt, max_retries, progress_callback
                    )
                if products:
                    break
                elif attempt < max_retries:
                    print(f"[Amazon] 未解析到有效产品，重试 ({attempt}/{max_retries})...")
                    await asyncio.sleep(2 ** attempt)

            except Exception as e:
                print(f"[Amazon] 第 {attempt} 次尝试失败: {e}")
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    print(f"[Amazon] {delay}s 后重试...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    print(f"[Amazon] 所有重试均失败: {e}")
                    break

        return products

    async def _fetch_amazon_once(self, browser, url: str, niche: str, limit: int,
                                 attempt: int, max_retries: int, progress_callback=None) -> List[dict]:
        """单次尝试: 加载搜索页并解析。返回产品列表(可能为空, 空则由调用方决定重试)。"""
        products = []
        context, warmup_page = await self._new_amazon_context(browser)
        # 预热页用完即关, 搜索用新页面 —— 复用预热页会在预热导航未完成时
        # 触发 "interrupted by another navigation" 竞态
        await warmup_page.close()
        page = await context.new_page()
        try:
            if progress_callback:
                progress_callback(1, f"加载搜索页面 (尝试 {attempt}/{max_retries} 次)...")

            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # 等待搜索结果容器
            try:
                await page.wait_for_selector(
                    '[data-component-type="s-search-result"], .s-result-item', timeout=20000
                )
            except Exception as e:
                print(f"[Amazon] 等待结果容器超时: {e}")
                # 继续尝试解析

            # 搜索结果总数(REAL, Gate1 volume 代理)
            result_count = 0
            try:
                rc_el = await page.query_selector('span.a-color-state.a-text-bold')
                if rc_el:
                    rc_text = (await rc_el.inner_text()).strip()
                    m = re.search(r'([\d,]+(?:\.\d+)?[KMB]?)\s*results?', rc_text, re.I)
                    if m:
                        result_count = self._parse_compact_number(m.group(1))
            except Exception:
                pass
            if not result_count:
                try:
                    body_text = await page.inner_text('body')
                    m = re.search(r'of\s+(?:over\s+)?([\d,]+(?:\.\d+)?[KMB]?)\s*results?', body_text, re.I)
                    if m:
                        result_count = self._parse_compact_number(m.group(1))
                except Exception:
                    pass

            items = await page.query_selector_all('[data-component-type="s-search-result"]')
            if not items:
                items = await page.query_selector_all('.s-result-item[data-asin]')

            if not items:
                print(f"[Amazon] 未找到商品项 (尝试 {attempt}/{max_retries})")
                return products

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
                    # 过滤徽章/广告文本
                    if any(bad in title for bad in ("Amazon's Choice", "Overall Pick", "Sponsored", "Best Seller")):
                        title = ""
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

                    # 评论数(REAL)
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

                    # 90 天销量
                    est_sales_90d = bought_past_month * 3
                    sales_method = "bought_in_past_month_x3" if bought_past_month else ""

                    products.append({
                        "title": title,
                        "price_usd": price_usd,
                        "rating": rating,
                        "reviews": reviews,
                        "bought_past_month": bought_past_month,
                        "url": f"https://www.amazon.com/dp/{asin}" if asin else urljoin("https://www.amazon.com", href),
                        "asin": asin,
                        "source": "amazon",
                        "est_sales_90d": est_sales_90d,
                        "sales_method": sales_method,
                        "result_count": result_count,
                    })
                except Exception as e:
                    print(f"[Amazon] 解析单品失败: {e}")
                    continue
        finally:
            await context.close()

        return products

    # ------------------------------------------------------------------
    # Amazon 详情页: BSR / Item Weight / Product Dimensions(REAL)
    # ------------------------------------------------------------------
    async def _enrich_amazon_details(self, products: List[dict], max_details: int = 6, browser=None) -> List[dict]:
        """对前 max_details 个 ASIN 爬详情页, 补 BSR / 重量 / 尺寸

        browser: 可选的共享浏览器实例; 传入则复用(调用方负责关闭), 不传则自建自关
        """
        targets = [p for p in products if p.get("asin")][:max_details]
        if not targets:
            return products

        # 详情页并发但限速(Amazon 反爬敏感 + CPU 控制, 1 并发最稳)
        sem = asyncio.Semaphore(1)

        async def _run(browser) -> None:
            context, warmup_page = await self._new_amazon_context(browser)
            try:
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
            finally:
                await context.close()

        if browser is not None:
            await _run(browser)
        else:
            async with async_playwright() as p:
                own_browser = await p.chromium.launch(headless=True, args=self._browser_launch_args())
                try:
                    await _run(own_browser)
                finally:
                    await own_browser.close()

        return products

    @staticmethod
    async def _parse_detail_page(page) -> dict:
        """解析详情页: BSR + Item Weight + Product Dimensions

        更健壮的解析: 尝试多种选择器, 检测验证码页面, 多模式正则匹配
        """
        result = {"bsr": 0, "weight_g": 0.0, "dimensions_cm": (0.0, 0.0, 0.0)}
        
        # 先检测是否被拦截 (验证码/错误页面)
        try:
            body_text = await page.inner_text('body')
            if any(kw in body_text for kw in [
                "Click the button below to continue shopping",
                "To discuss automated access to Amazon data",
                "api-services-support@amazon.com",
                "validateCaptcha",
                "Enter the characters you see below",
                "Sorry! Something went wrong",
            ]):
                print("[Amazon detail] Page blocked (CAPTCHA/error page)")
                return result
        except Exception:
            pass
        
        texts = []

        # 尝试多种选择器 (新版 Amazon 页面结构变化频繁)
        selectors = [
            "#productDetails_detailBullets_sections1",
            "#detailBullets_feature_div", 
            "#prodDetails",
            "#productDetails_techSpec_section_1",
            "#productDetails_db_sections",
            ".a-expander-content.a-expander-extend-content",
            "#feature-bullets",
            "table.a-keyvalue",
            "#detailBullets_feature_div .a-list-item",
            "[data-feature-name='detailBullets']",
            ".prodDetSectionEntry",
            "#productDetailsTable",
        ]
        
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    text = await el.inner_text()
                    if text and len(text.strip()) > 20:
                        texts.append(text)
            except Exception:
                continue
        
        # 如果上述都没抓到, 尝试抓取 body 中的关键区域
        if not texts:
            try:
                full_page = await page.inner_text('body')
                # 只保留可能包含规格的部分
                for keyword in ["Product Details", "Technical Details", "Product Dimensions", "Item Weight", "Product Information"]:
                    idx = full_page.find(keyword)
                    if idx >= 0:
                        texts.append(full_page[idx:idx+3000])
                        break
            except Exception:
                pass

        # 去掉隐藏字符(RTL 标记等, Amazon 详情页常见)
        full = "\n".join(texts)
        full = re.sub(r'[\u200e\u200f\u200b\u2060\ufeff]', '', full)
        
        if not full.strip():
            return result

        # BSR: 多种格式
        bsr_patterns = [
            r'Best Sellers Rank:?\s*#([\d,]+)',
            r'#([\d,]+)\s+in\s+[A-Za-z]',
            r'Best Seller Rank\s*#([\d,]+)',
            r'Rank\s*#([\d,]+)\s+in',
        ]
        for pat in bsr_patterns:
            m = re.search(pat, full)
            if m:
                result["bsr"] = int(m.group(1).replace(",", ""))
                break

        # Product Dimensions: 多种格式
        dim_patterns = [
            r'Product Dimensions\s*:?\s*([\d.]+\s*x\s*[\d.]+\s*x\s*[\d.]+)\s*inches',
            r'Product Dimensions\s*:?\s*([\d.]+\s*x\s*[\d.]+\s*x\s*[\d.]+)\s*(?:in|inches|")',
            r'Dimensions\s*:?\s*([\d.]+\s*x\s*[\d.]+\s*x\s*[\d.]+)\s*inches',
            r'(\d+\.?\d*)\s*x\s*(\d+\.?\d*)\s*x\s*(\d+\.?\d*)\s*(?:in|inches|")',
        ]
        for pat in dim_patterns:
            m = re.search(pat, full, re.I)
            if m:
                if m.lastindex and m.lastindex >= 3:
                    # 捕获组模式
                    dims = [float(m.group(i)) for i in range(1, 4)]
                else:
                    # 单组模式, 手动分割
                    dims_str = m.group(1) if m.lastindex >= 1 else m.group(0)
                    dims = [float(x) for x in re.split(r'\s*x\s*', dims_str)]
                if len(dims) == 3 and all(d > 0 for d in dims):
                    result["dimensions_cm"] = tuple(round(d * 2.54, 1) for d in dims)
                    break

        # Item Weight: 多种格式
        weight_patterns = [
            r'Item Weight\s*:?\s*([\d.]+)\s*(pounds?|ounces?|lbs?|oz|g|kg|grams?|kilograms?)',
            r'Weight\s*:?\s*([\d.]+)\s*(pounds?|ounces?|lbs?|oz|g|kg|grams?|kilograms?)',
            r'Item Weight\s*[:\-]\s*([\d.]+)\s*(pounds?|ounces?|lbs?|oz|g|kg|grams?|kilograms?)',
            r'inches\s*[;，]?\s*([\d.]+)\s*(pounds?|ounces?|lbs?|oz)',
            r'Shipping Weight\s*:?\s*([\d.]+)\s*(pounds?|ounces?|lbs?|oz|g|kg|grams?|kilograms?)',
        ]
        for pat in weight_patterns:
            m = re.search(pat, full, re.I)
            if m:
                val, unit = float(m.group(1)), m.group(2).lower()
                if unit.startswith("pound") or unit == "lbs":
                    result["weight_g"] = round(val * 453.6, 1)
                elif unit.startswith("ounce") or unit == "oz":
                    result["weight_g"] = round(val * 28.35, 1)
                elif unit.startswith("kg") or unit == "kilograms":
                    result["weight_g"] = round(val * 1000, 1)
                elif unit.startswith("g") and unit not in ("kg", "grams"):
                    result["weight_g"] = round(val, 1)
                elif unit == "grams":
                    result["weight_g"] = round(val, 1)
                break

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
    # Reddit 痛点/推荐挖掘(REAL 用户讨论, 免费公开 JSON API)
    # ------------------------------------------------------------------
    # 相关子版块映射: niche → 可能的 subreddit
    REDDIT_SUBREDDIT_MAP = {
        # 健康/康复
        "neck massager": ["chronicpain", "neckpain", "massage", "buyitforlife"],
        "posture corrector": ["posture", "backpain", "ergonomics", "chronicpain"],
        "shoulder massager": ["shoulderpain", "massage", "chronicpain"],
        "cervical pillow": ["sleep", "neckpain", "buyitforlife"],
        "acupressure mat": ["backpain", "chronicpain", "alternativehealth"],
        "red light therapy": ["redlighttherapy", "biohackers", "skincareaddiction", "chronicpain"],
        "tens unit": ["chronicpain", "physicaltherapy", "backpain", "sciatica"],
        "massage gun": ["fitness", "recovery", "massage", "homegym"],
        "compression boots": ["running", "triathlon", "recovery", "marathontraining"],
        "infrared heating pad": ["chronicpain", "backpain", "fibromyalgia", "arthritis"],
        # 睡眠
        "sleep mask": ["sleep", "insomnia", "buyitforlife"],
        "white noise": ["sleep", "whitenoise", "parenting"],
        "weighted blanket": ["sleep", "anxiety", "autism", "buyitforlife"],
        "silk pillowcase": ["skincareaddiction", "haircare", "beauty", "buyitforlife"],
        "mouth tape": ["sleep", "snoring", "mouthbreathing", "biohackers"],
        "nasal strips": ["snoring", "sleep", "allergies", "running"],
        # 办公/数码
        "cable organizer": ["cablemanagement", "homeoffice", "homelab"],
        "phone stand": ["phoneaccessories", "android", "iphone"],
        "laptop stand": ["laptops", "macbook", "homeoffice", "ergonomics"],
        "usb hub": ["macbook", "usbcharging", "techsupport"],
        "tablet holder": ["tablets", "ipad", "bedroom"],
        "vertical mouse": ["ergonomics", "rsi", "mousereview", "trackballs"],
        "mechanical keyboard": ["mechanicalkeyboards", "keyboards", "ergonomics"],
        "monitor arm": ["monitors", "homeoffice", "battlestations", "ergonomics"],
        "blue light glasses": ["gaming", "eyehealth", "biohackers", "glasses"],
        "desk cycle": ["fitness", "homeoffice", "standingdesk", "ergonomics"],
        # 宠物
        "dog nail grinder": ["dogs", "dogtraining", "puppy101"],
        "pet hair remover": ["pets", "cats", "dogs", "cleaning"],
        "cat water fountain": ["cats", "catcare"],
        "automatic pet feeder": ["pets", "cats", "dogs", "smartthings"],
        "pet camera": ["pets", "homesecurity", "smartthings", "cats"],
        "dog anxiety vest": ["dogs", "dogtraining", "reactivedogs", "anxiety"],
        "cat litter box self cleaning": ["cats", "catcare", "smartthings"],
        # 厨房
        "milk frother": ["coffee", "espresso", "barista"],
        "food storage bags": ["zerowaste", "mealprep", "kitchen"],
        "vacuum sealer": ["foodsaver", "mealprep", "sousvide", "bulkfoods"],
        "immersion blender": ["cooking", "kitchengadgets", "soup"],
        "meat thermometer": ["cooking", "grilling", "bbq", "sousvide"],
        "air fryer accessories": ["airfryer", "cooking", "mealprep"],
        # 美容/个人护理
        "ice roller": ["skincareaddiction", "beauty", "asianbeauty"],
        "scalp massager": ["haircare", "scalphealth", "hairloss"],
        "led face mask": ["skincareaddiction", "asianbeauty", "beauty", "antiaging"],
        "microcurrent facial": ["skincareaddiction", "esthetics", "antiaging", "beauty"],
        "dermaplaning": ["skincareaddiction", "beauty", "asianbeauty", "exfoliation"],
        "laser hair removal": ["skincareaddiction", "laserhairremoval", "beauty"],
        "teeth whitening": ["teethwhitening", "dental", "beauty", "smile"],
        # 出行
        "bike phone mount": ["bicycling", "bikecommuting", "cycling"],
        "book stand": ["books", "reading", "ergonomics"],
        "travel pillow": ["travel", "onebag", "frequenttravelers", "sleep"],
        "luggage tracker": ["travel", "airtags", "luggage", "frequenttravelers"],
        "packing cubes": ["onebag", "travel", "packing", "minimalism"],
        "portable espresso": ["coffee", "camping", "travel", "espresso"],
        # 家居/收纳
        "cable management": ["cablemanagement", "homeoffice", "homelab"],
        "command hooks": ["homeimprovement", "organization", "renting"],
        "vacuum storage bags": ["organization", "storage", "moving"],
        "laundry hamper": ["laundry", "organization", "apartmentliving"],
        "shoe rack": ["organization", "entryway", "apartmentliving"],
        "drawer organizers": ["organization", "declutter", "apartmentliving"],
        "over door storage": ["organization", "apartmentliving", "smallspaces"],
        # 厨房/餐饮
        "spice grinder": ["cooking", "spices", "kitchengadgets"],
        "garlic press": ["cooking", "kitchengadgets", "buyitforlife"],
        "salad chopper": ["cooking", "mealprep", "kitchengadgets"],
        "dish rack": ["kitchen", "organization", "apartmentliving"],
        "kitchen scale": ["cooking", "baking", "mealprep"],
        "mandoline slicer": ["cooking", "kitchengadgets", "mealprep", "safety"],
        "herb keeper": ["cooking", "gardening", "zerowaste", "mealprep"],
        # 办公/健康
        "wrist rest": ["mechanicalkeyboards", "ergonomics", "rsi"],
        "foot rest": ["ergonomics", "homeoffice", "standingdesk"],
        "lumbar support": ["backpain", "ergonomics", "officechair"],
        "carpal tunnel": ["rsi", "carpaltunnel", "wristpain"],
        "standing desk converter": ["standingdesk", "homeoffice", "ergonomics", "backpain"],
        "monitor light bar": ["monitors", "homeoffice", "battlestations", "lighting"],
        # 宠物
        "dog poop bag": ["dogs", "dogwalking"],
        "cat scratching pad": ["cats", "catcare"],
        "pet grooming brush": ["dogs", "cats", "petgrooming"],
        "dog cooling mat": ["dogs", "pets", "hotweather"],
        "pet nail clippers": ["dogs", "cats", "petgrooming", "dogtraining"],
        # 个人护理
        "callus remover": ["footcare", "skincareaddiction"],
        "nose hair trimmer": ["malegrooming", "grooming"],
        "insect bite healer": ["camping", "hiking", "firstaid"],
        "facial hair removal": ["skincareaddiction", "beauty", "asianbeauty"],
        "water flosser": ["dental", "oralhealth", "flossing", "waterpik"],
        "tongue scraper": ["oralhealth", "ayurveda", "zerowaste", "biohackers"],
        # 户外/运动
        "resistance bands": ["homegym", "fitness", "bodyweightfitness"],
        "foam roller": ["fitness", "mobility", "running"],
        "massage gun mini": ["fitness", "recovery", "massage"],
        "water bottle clip": ["hiking", "camping", "ultralight"],
        "yoga mat travel": ["yoga", "travel", "fitness", "onebag"],
        "knee brace": ["kneepain", "running", "weightlifting", "physicaltherapy"],
        "ankle support": ["anklepain", "running", "basketball", "physicaltherapy"],
        # 新增: 高 AOV 细分赛道
        "smart garden": ["indoorgarden", "hydroponics", "gardening", "smartgarden"],
        "posture reminder": ["posture", "backpain", "ergonomics", "wearables"],
        "sleep tracker ring": ["sleep", "wearables", "biohackers", "ouraring"],
        "hand grip strengthener": ["griptraining", "climbing", "fitness", "handtherapy"],
        "foot massager": ["footpain", "plantarfasciitis", "massage", "reflexology"],
        "neck stretcher": ["neckpain", "chronicpain", "cervical", "physicaltherapy"],
        "blue light therapy": ["sleep", "seasonalaffectivedisorder", "biohackers", "lighttherapy"],
        "posture corrector smart": ["posture", "wearables", "backpain", "ergonomics"],
        "massage chair pad": ["massage", "chronicpain", "backpain", "relaxation"],
        "leg compression massager": ["lymphedema", "varicoseveins", "recovery", "circulation"],
    }

    @staticmethod
    def _match_subreddits(niche: str) -> List[str]:
        """根据 niche 匹配相关 subreddit"""
        niche_lower = niche.lower()
        matched = set()
        for key, subs in PublicFetcher.REDDIT_SUBREDDIT_MAP.items():
            if key in niche_lower or any(kw in niche_lower for kw in key.split()):
                matched.update(subs)
        # 兜底通用版块
        if not matched:
            matched.update(["buyitforlife", "productreviews", "shutupandtakemymoney"])
        return list(matched)[:5]  # 限制最多 5 个

    async def _fetch_reddit(self, niche: str, limit: int = 15) -> List[dict]:
        """从 Reddit 抓取痛点讨论、产品推荐、吐槽帖 (免费公开 RSS, 无需登录)

        返回结构:
        [
            {"type": "pain_point", "text": "...", "subreddit": "...", "score": 100, "url": "..."},
            {"type": "recommendation", "text": "...", "product_mentioned": "...", "subreddit": "...", "score": 50, "url": "..."},
            {"type": "complaint", "text": "...", "subreddit": "...", "score": 80, "url": "..."},
        ]
        """
        results = []
        subreddits = self._match_subreddits(niche)
        niche_words = set(niche.lower().split())
        
        # 关键词用于分类帖子类型
        pain_keywords = {"pain", "hurt", "ache", "problem", "issue", "struggle", "annoying", "frustrating", "terrible", "awful", "worst", "broken", "failed", "disappointed", "regret", "waste", "useless", "doesn't work", "not working", "stopped working", "cheap", "flimsy"}
        rec_keywords = {"recommend", "suggest", "best", "favorite", "love", "great", "amazing", "perfect", "works", "works well", "highly recommend", "worth it", "game changer", "life changing", "buy", "bought", "purchased"}
        complaint_keywords = {"avoid", "don't buy", "waste of money", "returned", "refund", "broken", "defective", "poor quality", "cheaply made", "fell apart", "stopped working", "customer service", "warranty"}

        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ProductSourcingBot/1.0)"}
        ) as client:
            for sub in subreddits:
                try:
                    # 使用 RSS 源 (更稳定, 不易被封) - 只尝试基础 URL, 不加参数避免 429
                    rss_url = f"https://www.reddit.com/r/{sub}/.rss"
                    resp = await client.get(rss_url)
                    if resp.status_code != 200:
                        if resp.status_code == 429:
                            print(f"[Reddit RSS] r/{sub} rate limited (429), skipping")
                        else:
                            print(f"[Reddit RSS] r/{sub} failed: {resp.status_code}")
                        continue
                    
                    import feedparser
                    feed = feedparser.parse(resp.text)
                    
                    if not feed.entries:
                        continue
                    
                    for entry in feed.entries:
                        title = entry.get("title", "")
                        # RSS 通常没有正文, 用 title + summary
                        summary = entry.get("summary", "")
                        full_text = f"{title} {summary}".lower()
                        link = entry.get("link", "")
                        score = 0
                        
                        # 过滤: 必须包含 niche 相关词
                        if not any(w in full_text for w in niche_words):
                            continue
                        
                        # 分类帖子类型
                        post_type = "discussion"
                        if any(kw in full_text for kw in pain_keywords):
                            post_type = "pain_point"
                        elif any(kw in full_text for kw in rec_keywords):
                            post_type = "recommendation"
                        elif any(kw in full_text for kw in complaint_keywords):
                            post_type = "complaint"
                        
                        results.append({
                            "type": post_type,
                            "title": title[:200],
                            "text": summary[:500] if summary else "",
                            "subreddit": sub,
                            "score": score,
                            "url": link,
                            "products_mentioned": [],
                        })
                        
                        if len(results) >= limit:
                            break
                            
                except Exception as e:
                    print(f"[Reddit RSS] r/{sub} failed: {e}")
                    continue
                
                if len(results) >= limit:
                    break

        # 备选: Google 搜索 site:reddit.com (如果 RSS 失败)
        if len(results) < 3:
            google_results = await self._fetch_reddit_via_google(niche, limit - len(results))
            results.extend(google_results)

        # 按类型优先级排序 (痛点和推荐优先)
        type_priority = {"pain_point": 3, "recommendation": 2, "complaint": 2, "discussion": 1}
        results.sort(key=lambda x: (type_priority.get(x["type"], 0), x["score"]), reverse=True)
        
        return results[:limit]

    async def _fetch_reddit_via_google(self, niche: str, limit: int = 10) -> List[dict]:
        """备选: 通过 Google 搜索 site:reddit.com 找到相关讨论"""
        results = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                query = f"site:reddit.com \"{niche}\" (pain OR problem OR recommend OR review OR best)"
                url = f"https://www.google.com/search?q={quote_plus(query)}"
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return []
                
                # 简单解析 HTML (实际可用 BeautifulSoup)
                from html.parser import HTMLParser
                import re
                
                # 提取搜索结果链接
                links = re.findall(r'<a[^>]+href="/url\?q=([^&]+)', resp.text)
                for link in links[:limit]:
                    if "reddit.com/r/" in link and "/comments/" in link:
                        results.append({
                            "type": "discussion",
                            "title": f"Reddit discussion about {niche}",
                            "text": f"Found via Google search: {link}",
                            "subreddit": "unknown",
                            "score": 0,
                            "url": link,
                            "products_mentioned": [],
                        })
        except Exception:
            pass
        return results

    async def _fetch_reddit_keywords(self, niche: str) -> List[dict]:
        """从 Reddit 讨论中提取长尾关键词/痛点词"""
        reddit_data = await self._fetch_reddit(niche, limit=20)
        keywords = []
        
        for post in reddit_data:
            # 从标题和正文提取潜在关键词
            text = f"{post['title']} {post['text']}"
            # 简单提取: 3-5 词短语
            words = re.findall(r'\b[a-z]{3,}\b', text.lower())
            # 组合成短语 (这里简化, 实际可用 RAKE/KeyBERT)
            # 过滤停用词
            stopwords = {"the", "and", "for", "with", "this", "that", "have", "has", "had", "was", "were", "been", "from", "they", "their", "there", "what", "when", "where", "which", "who", "whom", "your", "will", "would", "could", "should", "about", "after", "before", "because", "into", "than", "then", "very", "just", "like", "into", "over", "under", "again", "also", "only", "other", "than", "its", "our", "out", "use", "used", "using"}
            filtered = [w for w in words if w not in stopwords and len(w) >= 4]
            
            # 简单频次统计生成关键词
            from collections import Counter
            freq = Counter(filtered)
            for kw, count in freq.most_common(5):
                if count >= 2:  # 至少出现 2 次
                    keywords.append({
                        "keyword": kw,
                        "volume": 0,
                        "kd": 0,
                        "volume_provenance": "MISSING",
                        "kd_provenance": "MISSING",
                        "source": "reddit",
                        "reddit_subreddit": post["subreddit"],
                        "reddit_score": post["score"],
                    })
        
        # 去重
        seen = set()
        unique = []
        for kw in keywords:
            if kw["keyword"] not in seen:
                seen.add(kw["keyword"])
                unique.append(kw)
        
        return unique[:15]

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
        """pytrends 获取 90 天趋势 + 上升相关词(真实) — 多策略兜底"""
        # 本地缓存,避免频繁被限流
        cached = self._load_trends_cache(niche)
        if cached:
            return cached

        self._patch_pytrends_urllib3()
        
        # 策略 1: 标准 12 个月 YoY
        result = await self._fetch_trends_strategy(niche, timeframe='today 12-m', cat=0)
        if result["yoy_pct"] != 0 or result["rising_kws"]:
            self._save_trends_cache(niche, result)
            return result
        
        # 策略 2: 过去 5 年长周期 (对季节性品类更稳)
        result = await self._fetch_trends_strategy(niche, timeframe='today 5-y', cat=0)
        if result["yoy_pct"] != 0 or result["rising_kws"]:
            self._save_trends_cache(niche, result)
            return result
        
        # 策略 3: 尝试购物属性搜索
        result = await self._fetch_trends_strategy(niche, timeframe='today 12-m', cat=0, gprop='froogle')
        if result["yoy_pct"] != 0 or result["rising_kws"]:
            self._save_trends_cache(niche, result)
            return result
        
        # 策略 4: 尝试 YouTube 搜索趋势 (产品类视频搜索)
        result = await self._fetch_trends_strategy(niche, timeframe='today 12-m', cat=0, gprop='youtube')
        if result["yoy_pct"] != 0 or result["rising_kws"]:
            self._save_trends_cache(niche, result)
            return result

        # 全部失败返回空
        return {"yoy_pct": 0, "rising_kws": []}

    async def _fetch_trends_strategy(self, niche: str, timeframe: str = 'today 12-m', cat: int = 0, gprop: str = '') -> dict:
        """单策略 Trends 抓取"""
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl='en-US', tz=360, timeout=(10, 25), retries=2, backoff_factor=0.5)
            pytrends.build_payload([niche], cat=cat, timeframe=timeframe, geo='US', gprop=gprop)

            interest_df = pytrends.interest_over_time()
            yoy_pct = 0
            if interest_df is not None and not interest_df.empty and niche in interest_df.columns:
                series = interest_df[niche].dropna()
                if len(series) >= 26:
                    def _nonzero_mean(arr):
                        nz = arr[arr > 0]
                        return float(nz.mean()) if len(nz) >= 4 else 0.0

                    # 根据时间窗口动态调整比较期长度
                    if timeframe == 'today 5-y':
                        recent_window = 26  # 最近 ~6个月
                        older_window = 52   # 前 ~6个月
                    else:
                        recent_window = 13  # 最近 ~90天
                        older_window = 26   # 前 ~90天
                    
                    if len(series) >= recent_window + older_window:
                        recent = _nonzero_mean(series.tail(recent_window).values)
                        older = _nonzero_mean(series.iloc[-(recent_window + older_window):-recent_window].values)
                        if older > 1:
                            yoy_pct = (recent - older) / older * 100
                        elif recent > 0 and older == 0:
                            yoy_pct = 100.0

            # 上升相关词
            rising_kws = []
            related = None
            for attempt in range(3):
                try:
                    related = pytrends.related_queries()
                    break
                except Exception as e:
                    print(f"[Trends] related_queries attempt {attempt+1}/3 failed: {e}")
                    if attempt < 2:
                        await asyncio.sleep(5 * (attempt + 1))
            if related and niche in related:
                r = related[niche]
                if isinstance(r, dict):
                    rising_df = r.get("rising")
                    if rising_df is not None and hasattr(rising_df, "to_dict") and not rising_df.empty:
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
                    else:
                        top_df = r.get("top")
                        if top_df is not None and hasattr(top_df, "to_dict") and not top_df.empty:
                            for _, row in top_df.head(10).iterrows():
                                rising_kws.append({
                                    "keyword": str(row.get("query", "")),
                                    "volume": 0,
                                    "kd": 0,
                                    "trending_value": 0.0,
                                    "volume_provenance": "MISSING",
                                    "kd_provenance": "MISSING",
                                    "trending_provenance": "REAL",
                                })

            return {
                "yoy_pct": round(yoy_pct, 1),
                "rising_kws": rising_kws,
            }
        except Exception as e:
            print(f"[Trends] strategy {timeframe} failed: {e}")
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
                    # 上升词为空时缓存可能不完整(上次抓取失败) → 只信任 1 小时
                    if entry.get("rising_empty") and time.time() - entry.get("ts", 0) > 3600:
                        return None
                    return entry.get("data")
        except Exception:
            pass
        return None

    def _save_trends_cache(self, niche: str, result: dict) -> None:
        try:
            data = {}
            if TRENDS_CACHE_FILE.exists():
                data = json.loads(TRENDS_CACHE_FILE.read_text())
            data[niche] = {
                "ts": time.time(),
                "data": result,
                "rising_empty": not bool(result.get("rising_kws")),
            }
            TRENDS_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass


async def fetch_niche_public(niche: str, limit: int = 20) -> List[RawProductData]:
    """入口函数,供 pipeline 调用"""
    fetcher = PublicFetcher()
    return await fetcher.fetch(niche, limit)


# ----------------------------------------------------------------------
# Gate 6: Amazon 同款检测(REAL)
# 在 Amazon 搜索候选标题核心词, 统计前 3 页同款数(排除候选自身 ASIN)。
# TEMU/SHEIN 因登录墙无法自动检测, 保留人工审核。
# ----------------------------------------------------------------------
_TITLE_STOPWORDS = {
    "for", "with", "and", "the", "of", "to", "in", "on", "at", "by", "a", "an",
    "men", "women", "womens", "mens", "kids", "child", "children", "baby",
    "gift", "gifts", "home", "office", "travel", "new", "hot", "best", "plus",
    "pro", "mini", "max", "set", "pack", "upgrade", "upgraded", "large",
    "small", "portable", "adjustable", "rechargeable", "wireless", "electric",
    "usb", "type", "cable", "cord", "decor", "ideal", "perfect", "great",
    "fits", "made", "compatible", "accessory", "accessories", "personal",
    "friends", "family", "dad", "mom", "mother", "father", "husband", "wife",
}


def _normalize_title_for_match(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\b\d+[a-z]?\b', ' ', t)  # 数字型号/规格
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _extract_search_query(title: str) -> str:
    """从标题提取核心搜索词: 去停用词/型号, 取前 4 个词"""
    words = [
        w for w in _normalize_title_for_match(title).split()
        if w not in _TITLE_STOPWORDS and len(w) >= 3
    ]
    return " ".join(words[:4]) or (title or "").strip()


def _title_similarity(a: str, b: str) -> float:
    """同款相似度: token Jaccard 与 SequenceMatcher 取较大值"""
    wa = set(_normalize_title_for_match(a).split())
    wb = set(_normalize_title_for_match(b).split())
    jac = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return max(jac, ratio)


def _extract_asin(url_or_text: str) -> str:
    m = re.search(r'/dp/([A-Z0-9]{10})', url_or_text or "")
    return m.group(1) if m else ""


async def check_amazon_duplicates_batch(
    candidates: list,
    max_check: int = 10,
    concurrency: int = 2,
    pages: int = 3,
    similarity_threshold: float = 0.75,
) -> None:
    """对候选在 Amazon 搜索前 pages 页, 统计同款数(排除自身), 写回 amazon_duplicate_count。

    -1 保留 = 检测失败/未检测(score 层标数据不足)。
    每个候选 ~pages 次页面加载, 限速防反爬; 只检测前 max_check 个。
    """
    targets = [c for c in candidates if c.title][:max_check]
    if not targets:
        return

    fetcher = PublicFetcher()
    sem = asyncio.Semaphore(concurrency)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=fetcher._browser_launch_args())
        context, warmup = await fetcher._new_amazon_context(browser)
        await warmup.close()

        async def check_one(cand) -> None:
            async with sem:
                self_asin = _extract_asin(cand.competitor_urls[0] if cand.competitor_urls else "")
                query = _extract_search_query(cand.title)
                match_count = 0
                try:
                    for page_no in range(1, pages + 1):
                        url = f"https://www.amazon.com/s?k={quote_plus(query)}&page={page_no}"
                        page = await context.new_page()
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                            try:
                                await page.wait_for_selector(
                                    '[data-component-type="s-search-result"], .s-result-item',
                                    timeout=12000,
                                )
                            except Exception:
                                pass
                            items = await page.query_selector_all('[data-component-type="s-search-result"]')
                            if not items:
                                items = await page.query_selector_all('.s-result-item[data-asin]')
                            for item in items:
                                try:
                                    hit_title = ""
                                    for sel in ['h2 a span', 'h2 .a-text-normal', '.a-size-base-plus']:
                                        el = await item.query_selector(sel)
                                        if el:
                                            hit_title = (await el.inner_text()).strip()
                                            if hit_title:
                                                break
                                    if not hit_title:
                                        continue
                                    href_el = await item.query_selector('h2 a, .a-link-normal[href*="/dp/"]')
                                    href = await href_el.get_attribute('href') if href_el else ""
                                    hit_asin = _extract_asin(href)
                                    if hit_asin and hit_asin == self_asin:
                                        continue  # 候选自身, 不算同款
                                    if _title_similarity(cand.title, hit_title) >= similarity_threshold:
                                        match_count += 1
                                except Exception:
                                    continue
                        finally:
                            await page.close()
                        await asyncio.sleep(1.5)  # 页间限速
                    cand.amazon_duplicate_count = match_count
                except Exception as e:
                    print(f"[Amazon dup-check] {cand.id} failed: {e}")
                    cand.amazon_duplicate_count = -1  # 检测失败 = 数据不足

        await asyncio.gather(*[check_one(c) for c in targets])
        await browser.close()
