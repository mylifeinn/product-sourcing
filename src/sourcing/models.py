from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid
import json


@dataclass
class ProductCandidate:
    """Normalized product candidate after fetch + enrich"""
    id: str = field(default_factory=lambda: f"SRC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}")
    title: str = ""
    niche: str = ""
    pain_point_keywords: list[str] = field(default_factory=list)
    
    # Gate 1: Pain Point
    trend_score: float = 0.0
    longtail_keywords: list[dict] = field(default_factory=list)  # [{kw, volume, kd}]
    
    # Gate 2: Trend
    google_trends_yoy_pct: float = 0.0
    tiktok_hashtag_growth_pct: float = 0.0
    # Amazon 单品趋势数据 [REAL]
    amazon_bsr: int = 0  # Best Sellers Rank(详情页 REAL)
    amazon_result_count: int = 0  # niche 搜索结果总数(搜索页 REAL, Gate1 volume 代理)
    
    # Gate 3: Margin
    wholesale_price_usd: float = 0.0
    estimated_retail_price_usd: float = 0.0
    estimated_shipping_usd: float = 0.0
    estimated_margin_pct: float = 0.0
    
    # Gate 4: Lightweight
    weight_g: float = 0.0
    dimensions_cm: tuple[float, float, float] = (0, 0, 0)
    shipping_channel: str = ""
    
    # Gate 5: Quality
    supplier_rating: float = 0.0
    refund_rate_pct: float = 0.0
    has_actual_photos: bool = False
    supplier_url: str = ""
    supplier_contact: str = ""
    
    # Gate 6: Uniqueness
    uniqueness_passed: bool = False
    competitor_urls: list[str] = field(default_factory=list)
    amazon_duplicate_count: int = -1  # Amazon 前3页同款数(-1=未检测, >=0=REAL 检测结果)
    
    # Gate 7: Seasonality
    is_evergreen: bool = True
    seasonal_peak_window_days: int = 0
    prep_lead_time_days: int = 0
    
    # Gate 8: Market Proof
    competitor_sales_90d: int = 0
    competitor_reviews: int = 0
    market_proof_urls: list[str] = field(default_factory=list)
    amazon_rating: float = 0.0  # Amazon 竞品评分 [REAL], 免费模式 Gate5 质量代理
    
    # Gate 9: Customer Value
    estimated_aov_usd: float = 0.0
    estimated_repurchase_cycle_days: int = 0
    estimated_ltv_orders: float = 0.0
    
    # Metadata
    source: str = ""  # 数据来源标识
    
    # Compliance
    patent_risk_level: str = "none"  # none, low, high
    trademark_risk_level: str = "none"
    matched_patents: list[str] = field(default_factory=list)
    
    # Scoring
    gate_results: dict[str, bool] = field(default_factory=dict)
    gate_details: dict[str, str] = field(default_factory=dict)  # 每个 gate 的判定依据说明
    total_score: int = 0
    passed_all_gates: bool = False
    needs_manual_review: bool = False  # 有数据不足的 gate, 需人工补充确认
    data_completeness_pct: float = 0.0  # REAL/ESTIMATED 字段占比
    data_provenance: dict[str, str] = field(default_factory=dict)  # 字段→REAL/ESTIMATED/MISSING
    
    # Review
    review_status: str = "pending"  # pending, approved, rejected, waived
    review_notes: str = ""
    
    # Shopify
    shopify_draft_id: Optional[int] = None
    shopify_product_id: Optional[int] = None
    published_at: Optional[datetime] = None
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_notion_properties(self) -> dict:
        """Convert to Notion API properties format"""
        # gate 三态中 None(数据不足) → Notion checkbox 只接受 bool, 转 False
        def _cb(name: str) -> bool:
            return self.gate_results.get(name) is True

        return {
            "Candidate ID": {"title": [{"text": {"content": self.id}}]},
            "产品标题": {"rich_text": [{"text": {"content": self.title}}]},
            "细分品类": {"select": {"name": self.niche}},
            "痛点关键词": {"multi_select": [{"name": kw} for kw in self.pain_point_keywords]},
            "Gate 1 痛点词": {"checkbox": _cb("gate_1")},
            "Gate 2 趋势": {"checkbox": _cb("gate_2")},
            "Gate 3 毛利": {"checkbox": _cb("gate_3")},
            "Gate 4 轻便": {"checkbox": _cb("gate_4")},
            "Gate 5 质量": {"checkbox": _cb("gate_5")},
            "Gate 6 独特性": {"checkbox": _cb("gate_6")},
            "Gate 7 长青性": {"checkbox": _cb("gate_7")},
            "Gate 8 验证": {"checkbox": _cb("gate_8")},
            "Gate 9 客户价值": {"checkbox": _cb("gate_9")},
            "专利风险": {"select": {"name": self.patent_risk_level}},
            "商标风险": {"select": {"name": self.trademark_risk_level}},
            "总分": {"number": self.total_score},
            "通过状态": {"select": {"name": "通过" if self.passed_all_gates else "拒绝"}},
            "批发价": {"number": self.wholesale_price_usd},
            "建议零售价": {"number": self.estimated_retail_price_usd},
            "毛利率%": {"number": round(self.estimated_margin_pct, 1)},
            "重量(g)": {"number": self.weight_g},
            "尺寸(cm)": {"rich_text": [{"text": {"content": f"{self.dimensions_cm[0]}×{self.dimensions_cm[1]}×{self.dimensions_cm[2]}"}}]},
            "物流渠道": {"rich_text": [{"text": {"content": self.shipping_channel}}]},
            "供应商链接": {"url": self.supplier_url},
            "供应商联系人": {"rich_text": [{"text": {"content": self.supplier_contact}}]},
            "竞品验证链接": {"url": self.market_proof_urls[0] if self.market_proof_urls else ""},
            "审核状态": {"select": {"name": self.review_status}},
            "审核备注": {"rich_text": [{"text": {"content": self.review_notes}}]},
        }
    
    def to_shopify_draft(self, brand_config: dict) -> dict:
        """Convert to Shopify Draft Product payload"""
        from sourcing.seo.template import render_product_description
        
        body_html = render_product_description(
            title=self.title,
            niche=self.niche,
            pain_point_keywords=self.pain_point_keywords,
            specs={
                "weight_g": self.weight_g,
                "dimensions_cm": self.dimensions_cm,
                "material": "见详情页规格表",
            },
            brand_voice=brand_config.get("voice", ""),
            usp_framework=brand_config.get("usp_framework", ""),
            trust_anchors=brand_config.get("trust_anchors", ""),
        )
        
        # Use first pain point keyword for SEO description
        first_pain = self.pain_point_keywords[0] if self.pain_point_keywords else "核心痛点"
        
        return {
            "product": {
                "title": self.title,
                "body_html": body_html,
                "vendor": brand_config.get("vendor", "YourBrand"),
                "product_type": self.niche,
                "tags": ",".join(self.pain_point_keywords + [self.niche, "sourcing:auto"]),
                "status": "draft",
                "variants": [{
                    "price": f"{self.estimated_retail_price_usd:.2f}",
                    "inventory_management": "shopify",
                    "inventory_quantity": 999,
                    "weight": self.weight_g,
                    "weight_unit": "g",
                    "requires_shipping": True,
                }],
                "metafields": [
                    {
                        "namespace": "seo",
                        "key": "title",
                        "value": f"{self.title} | {brand_config.get('vendor', 'YourBrand')}",
                        "type": "single_line_text_field",
                    },
                    {
                        "namespace": "seo",
                        "key": "description",
                        "value": f"解决{first_pain}痛点的{self.niche}，{brand_config.get('usp_framework', '')}",
                        "type": "multi_line_text_field",
                    },
                    {
                        "namespace": "geo",
                        "key": "keywords",
                        "value": ",".join(self.pain_point_keywords),
                        "type": "json",
                    },
                ],
            }
        }


class CandidateDB(BaseModel):
    """Pydantic model for SQLite storage"""
    id: str
    title: str
    niche: str
    pain_point_keywords: str  # JSON string
    trend_score: float
    longtail_keywords: str  # JSON
    google_trends_yoy_pct: float
    tiktok_hashtag_growth_pct: float
    amazon_bsr: int
    amazon_result_count: int
    wholesale_price_usd: float
    estimated_retail_price_usd: float
    estimated_shipping_usd: float
    estimated_margin_pct: float
    weight_g: float
    dimensions_cm: str  # JSON
    shipping_channel: str
    supplier_rating: float
    refund_rate_pct: float
    has_actual_photos: bool
    supplier_url: str
    supplier_contact: str
    uniqueness_passed: bool
    competitor_urls: str  # JSON
    amazon_duplicate_count: int
    is_evergreen: bool
    seasonal_peak_window_days: int
    prep_lead_time_days: int
    competitor_sales_90d: int
    competitor_reviews: int
    market_proof_urls: str  # JSON
    amazon_rating: float
    estimated_aov_usd: float
    estimated_repurchase_cycle_days: int
    estimated_ltv_orders: float
    patent_risk_level: str
    trademark_risk_level: str
    matched_patents: str  # JSON
    gate_results: str  # JSON
    gate_details: str  # JSON
    total_score: int
    passed_all_gates: bool
    needs_manual_review: bool
    data_completeness_pct: float
    data_provenance: str  # JSON
    review_status: str
    review_notes: str
    shopify_draft_id: Optional[int]
    shopify_product_id: Optional[int]
    published_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    @classmethod
    def from_candidate(cls, c: ProductCandidate) -> "CandidateDB":
        return cls(
            id=c.id,
            title=c.title,
            niche=c.niche,
            pain_point_keywords=json.dumps(c.pain_point_keywords, ensure_ascii=False),
            trend_score=c.trend_score,
            longtail_keywords=json.dumps(c.longtail_keywords, ensure_ascii=False),
            google_trends_yoy_pct=c.google_trends_yoy_pct,
            tiktok_hashtag_growth_pct=c.tiktok_hashtag_growth_pct,
            amazon_bsr=c.amazon_bsr,
            amazon_result_count=c.amazon_result_count,
            wholesale_price_usd=c.wholesale_price_usd,
            estimated_retail_price_usd=c.estimated_retail_price_usd,
            estimated_shipping_usd=c.estimated_shipping_usd,
            estimated_margin_pct=c.estimated_margin_pct,
            weight_g=c.weight_g,
            dimensions_cm=json.dumps(list(c.dimensions_cm)),
            shipping_channel=c.shipping_channel,
            supplier_rating=c.supplier_rating,
            refund_rate_pct=c.refund_rate_pct,
            has_actual_photos=c.has_actual_photos,
            supplier_url=c.supplier_url,
            supplier_contact=c.supplier_contact,
            uniqueness_passed=c.uniqueness_passed,
            competitor_urls=json.dumps(c.competitor_urls),
            amazon_duplicate_count=c.amazon_duplicate_count,
            is_evergreen=c.is_evergreen,
            seasonal_peak_window_days=c.seasonal_peak_window_days,
            prep_lead_time_days=c.prep_lead_time_days,
            competitor_sales_90d=c.competitor_sales_90d,
            competitor_reviews=c.competitor_reviews,
            market_proof_urls=json.dumps(c.market_proof_urls),
            amazon_rating=c.amazon_rating,
            estimated_aov_usd=c.estimated_aov_usd,
            estimated_repurchase_cycle_days=c.estimated_repurchase_cycle_days,
            estimated_ltv_orders=c.estimated_ltv_orders,
            patent_risk_level=c.patent_risk_level,
            trademark_risk_level=c.trademark_risk_level,
            matched_patents=json.dumps(c.matched_patents),
            gate_results=json.dumps(c.gate_results),
            gate_details=json.dumps(c.gate_details),
            total_score=c.total_score,
            passed_all_gates=c.passed_all_gates,
            needs_manual_review=c.needs_manual_review,
            data_completeness_pct=c.data_completeness_pct,
            data_provenance=json.dumps(c.data_provenance),
            review_status=c.review_status,
            review_notes=c.review_notes,
            shopify_draft_id=c.shopify_draft_id,
            shopify_product_id=c.shopify_product_id,
            published_at=c.published_at,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
    
    def to_candidate(self) -> ProductCandidate:
        c = ProductCandidate()
        c.id = self.id
        c.title = self.title
        c.niche = self.niche
        c.pain_point_keywords = json.loads(self.pain_point_keywords)
        c.trend_score = self.trend_score
        c.longtail_keywords = json.loads(self.longtail_keywords)
        c.google_trends_yoy_pct = self.google_trends_yoy_pct
        c.tiktok_hashtag_growth_pct = self.tiktok_hashtag_growth_pct
        c.amazon_bsr = self.amazon_bsr
        c.amazon_result_count = self.amazon_result_count
        c.wholesale_price_usd = self.wholesale_price_usd
        c.estimated_retail_price_usd = self.estimated_retail_price_usd
        c.estimated_shipping_usd = self.estimated_shipping_usd
        c.estimated_margin_pct = self.estimated_margin_pct
        c.weight_g = self.weight_g
        c.dimensions_cm = tuple(json.loads(self.dimensions_cm))
        c.shipping_channel = self.shipping_channel
        c.supplier_rating = self.supplier_rating
        c.refund_rate_pct = self.refund_rate_pct
        c.has_actual_photos = self.has_actual_photos
        c.supplier_url = self.supplier_url
        c.supplier_contact = self.supplier_contact
        c.uniqueness_passed = self.uniqueness_passed
        c.competitor_urls = json.loads(self.competitor_urls)
        c.amazon_duplicate_count = self.amazon_duplicate_count
        c.is_evergreen = self.is_evergreen
        c.seasonal_peak_window_days = self.seasonal_peak_window_days
        c.prep_lead_time_days = self.prep_lead_time_days
        c.competitor_sales_90d = self.competitor_sales_90d
        c.competitor_reviews = self.competitor_reviews
        c.market_proof_urls = json.loads(self.market_proof_urls)
        c.amazon_rating = self.amazon_rating
        c.estimated_aov_usd = self.estimated_aov_usd
        c.estimated_repurchase_cycle_days = self.estimated_repurchase_cycle_days
        c.estimated_ltv_orders = self.estimated_ltv_orders
        c.patent_risk_level = self.patent_risk_level
        c.trademark_risk_level = self.trademark_risk_level
        c.matched_patents = json.loads(self.matched_patents)
        c.gate_results = json.loads(self.gate_results)
        c.gate_details = json.loads(self.gate_details)
        c.total_score = self.total_score
        c.passed_all_gates = self.passed_all_gates
        c.needs_manual_review = self.needs_manual_review
        c.data_completeness_pct = self.data_completeness_pct
        c.data_provenance = json.loads(self.data_provenance)
        c.review_status = self.review_status
        c.review_notes = self.review_notes
        c.shopify_draft_id = self.shopify_draft_id
        c.shopify_product_id = self.shopify_product_id
        c.published_at = self.published_at
        c.created_at = self.created_at
        c.updated_at = self.updated_at
        return c