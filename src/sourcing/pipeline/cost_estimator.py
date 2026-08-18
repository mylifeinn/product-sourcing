from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from sourcing.config import get_config


@dataclass
class CostEstimate:
    """反推成本估算结果"""
    # 输入
    competitor_retail_price_usd: float
    competitor_source: str  # amazon/temu/shein/shopify

    # 估算输出
    estimated_wholesale_cny_min: float
    estimated_wholesale_cny_max: float
    estimated_wholesale_usd_min: float
    estimated_wholesale_usd_max: float

    # 建议定价
    suggested_retail_usd_min: float
    suggested_retail_usd_max: float

    # 毛利率区间
    gross_margin_pct_min: float
    gross_margin_pct_max: float

    # 物流成本估算
    estimated_shipping_usd: float

    # 置信度
    confidence: str  # high/medium/low
    notes: str


class CostEstimator:
    """
    反推成本模型：
    - 竞品零售价 ÷ 3.5~5.0 = 批发价区间
    - 结合平台费、物流、缓冲 → 建议零售价、毛利率区间
    """

    # 不同渠道的典型倍率范围
    # 针对不同价格段调整: 高客单价产品批发倍率通常较低(品牌溢价大)
    CHANNEL_MULTIPLIERS = {
        "amazon": (3.5, 5.0),      # FBA 费用高，倍率大
        "shopify": (3.0, 4.5),     # DTC 无平台抽成，倍率小
        "temu": (2.5, 4.0),        # 极致压价
        "shein": (2.5, 4.0),
        "alibaba": (1.5, 3.0),     # 批发平台直接拿货
        "unknown": (3.0, 5.0),
    }
    
    # 高 AOV 产品的调整倍率 (零售价 ≥ $60 时适用)
    # 高端品牌通常零售价/批发价 = 2.5-3.5x (而非 3.5-5x)
    HIGH_AOV_MULTIPLIERS = {
        "amazon": (2.5, 3.5),
        "shopify": (2.2, 3.2),
        "temu": (2.0, 3.0),
        "shein": (2.0, 3.0),
        "alibaba": (1.3, 2.2),
        "unknown": (2.0, 3.5),
    }

    # 物流成本表（USD，基于重量档位）
    SHIPPING_COST_TABLE = {
        (0, 100): 3.0,      # <100g: ePacket ~$3
        (100, 200): 5.0,    # 100-200g: ePacket/4PX ~$5
        (200, 500): 8.0,    # 200-500g: 4PX/YunExpress ~$8
        (500, 1000): 12.0,  # 500g-1kg: 专线小包 ~$12
        (1000, 2000): 18.0, # 1-2kg: 专线/海运拼箱 ~$18
    }

    # 平台费率
    PLATFORM_FEE_PCT = 2.9   # Shopify Payments
    PLATFORM_FIXED_FEE = 0.30
    MARKETPLACE_FEE_PCT = 15.0  # Amazon FBA 推荐费+FBA费 约 15%
    BUFFER_PCT = 15.0  # 运费缓冲

    def __init__(self):
        self.config = get_config()

    def _get_multipliers(self, competitor_source: str, retail_price: float):
        """根据价格段选择倍率表"""
        if retail_price >= 60:  # 高 AOV
            return self.HIGH_AOV_MULTIPLIERS.get(competitor_source.lower(), self.HIGH_AOV_MULTIPLIERS["unknown"])
        return self.CHANNEL_MULTIPLIERS.get(competitor_source.lower(), self.CHANNEL_MULTIPLIERS["unknown"])

    def estimate_shipping(self, weight_g: float) -> float:
        """根据重量估算物流成本"""
        for (w_min, w_max), cost in self.SHIPPING_COST_TABLE.items():
            if w_min <= weight_g < w_max:
                return round(cost * (1 + self.BUFFER_PCT / 100), 2)
        return round(self.SHIPPING_COST_TABLE[(1000, 2000)] * (1 + self.BUFFER_PCT / 100), 2)

    def estimate(self,
                 competitor_retail_price_usd: float,
                 competitor_source: str = "unknown",
                 weight_g: float = 200,
                 dimensions_cm: tuple = (15, 10, 5),
                 is_fba: bool = False) -> CostEstimate:
        """主入口"""

        multipliers = self._get_multipliers(competitor_source, competitor_retail_price_usd)
        mult_min, mult_max = multipliers

        # 1. 反推批发价区间 (CNY -> USD 汇率 7.2)
        wholesale_usd_min = round(competitor_retail_price_usd / mult_max, 2)
        wholesale_usd_max = round(competitor_retail_price_usd / mult_min, 2)
        wholesale_cny_min = round(wholesale_usd_min * 7.2, 2)
        wholesale_cny_max = round(wholesale_usd_max * 7.2, 2)

        # 2. 物流成本
        shipping_usd = self.estimate_shipping(weight_g)

        # 3. 平台费
        if is_fba or competitor_source.lower() == "amazon":
            platform_fee_pct = self.MARKETPLACE_FEE_PCT
        else:
            platform_fee_pct = self.PLATFORM_FEE_PCT

        # 4. 建议零售价区间（我们要比竞品稍低或持平，但保证毛利）
        # 策略：零售价 = 批发价 * 目标倍率(3.5-4.5)
        target_mult_min, target_mult_max = 3.5, 4.5
        retail_min = round(wholesale_usd_min * target_mult_min, 2)
        retail_max = round(wholesale_usd_max * target_mult_max, 2)

        # 夹在竞品价格附近，避免定价过高无竞争力
        retail_min = min(retail_min, competitor_retail_price_usd * 0.95)
        retail_max = min(retail_max, competitor_retail_price_usd * 1.05)

        # 5. 毛利率计算
        def calc_margin(retail: float, wholesale: float) -> float:
            fee = retail * platform_fee_pct / 100 + self.PLATFORM_FIXED_FEE
            total_cost = wholesale + shipping_usd + fee
            return round((retail - total_cost) / retail * 100, 1)

        margin_min = calc_margin(retail_min, wholesale_usd_max)  # 最悲观
        margin_max = calc_margin(retail_max, wholesale_usd_min)  # 最乐观

        # 6. 置信度
        if competitor_source.lower() in ["amazon", "shopify"]:
            confidence = "medium"
        elif competitor_source.lower() in ["temu", "shein"]:
            confidence = "low"  # 价格极不稳定
        else:
            confidence = "low"

        notes = (
            f"Based on {competitor_source} retail ${competitor_retail_price_usd:.2f}. "
            f"Multiplier range: {mult_min}x-{mult_max}x. "
            f"Shipping est: ${shipping_usd:.2f} ({weight_g}g). "
            f"Platform fee: {platform_fee_pct}%. "
            f"Target retail: ${retail_min:.2f}-${retail_max:.2f}. "
            f"Margin range: {margin_min}%-{margin_max}%."
        )

        return CostEstimate(
            competitor_retail_price_usd=competitor_retail_price_usd,
            competitor_source=competitor_source,
            estimated_wholesale_cny_min=wholesale_cny_min,
            estimated_wholesale_cny_max=wholesale_cny_max,
            estimated_wholesale_usd_min=wholesale_usd_min,
            estimated_wholesale_usd_max=wholesale_usd_max,
            suggested_retail_usd_min=retail_min,
            suggested_retail_usd_max=retail_max,
            gross_margin_pct_min=margin_min,
            gross_margin_pct_max=margin_max,
            estimated_shipping_usd=shipping_usd,
            confidence=confidence,
            notes=notes,
        )

    def batch_estimate(self, products: list, weight_g: float = 200) -> list:
        """批量估算"""
        results = []
        for p in products:
            est = self.estimate(
                competitor_retail_price_usd=p.get("price_usd", 0),
                competitor_source=p.get("source", "unknown"),
                weight_g=weight_g,
            )
            results.append(est)
        return results


def enrich_with_cost_estimate(candidate, cost_estimate: CostEstimate):
    """把 CostEstimate 填回 ProductCandidate"""
    candidate.wholesale_price_usd = (cost_estimate.estimated_wholesale_usd_min + cost_estimate.estimated_wholesale_usd_max) / 2
    candidate.estimated_retail_price_usd = (cost_estimate.suggested_retail_usd_min + cost_estimate.suggested_retail_usd_max) / 2
    candidate.estimated_shipping_usd = cost_estimate.estimated_shipping_usd
    candidate.estimated_margin_pct = (cost_estimate.gross_margin_pct_min + cost_estimate.gross_margin_pct_max) / 2
    # 存原始估算对象供审核
    candidate.cost_estimate = cost_estimate
    return candidate