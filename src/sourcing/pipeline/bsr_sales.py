from __future__ import annotations

"""BSR → 月销量估算(公开行业近似表)

Amazon Best Sellers Rank (BSR) 是 Amazon 公开显示的排名。
本模块把 BSR 转换为月销量估算,使用公开行业常用的幂律近似
(参考 Jungle Scout / AMZScout 等工具公布的公开对照数据)。

⚠️ 重要:这是 ESTIMATED(估算)数据,不是真实销量。
    真实销量信号应优先使用 Amazon 搜索页的 "bought in past month" 徽章
    (REAL,如 "1K+ bought in past month")。
    本估算仅用于 BSR 可用而徽章缺失时,并在数据来源中标注 ESTIMATED。

幂律模型: monthly_sales = exp(a + b * ln(bsr))
锚点(公开行业近似):
  BSR 1,000   → ~1,000 单/月
  BSR 100,000 → ~15 单/月
"""

import math

# 幂律参数(由上述锚点拟合)
_A = 13.21
_B = -0.912


def estimate_monthly_sales_from_bsr(bsr: int) -> int:
    """BSR → 估算月销量(单位: 单/月)。

    Args:
        bsr: Amazon Best Sellers Rank(越小越畅销)

    Returns:
        估算月销量(整数,至少 1)。BSR<=0 时返回 0(数据缺失)。
    """
    if bsr is None or bsr <= 0:
        return 0
    if bsr == 1:
        # 类目第一,公开资料显示通常 5000-30000+/月,保守取 20000
        return 20000
    monthly = math.exp(_A + _B * math.log(bsr))
    return max(1, int(round(monthly)))


def estimate_sales_90d_from_bsr(bsr: int) -> int:
    """BSR → 估算 90 天销量(ESTIMATED)"""
    return estimate_monthly_sales_from_bsr(bsr) * 3


# 公开对照表(仅文档用途,便于人工核对)
PUBLIC_REFERENCE_TABLE = [
    (100, "~5,000-10,000 单/月"),
    (500, "~2,000 单/月"),
    (1_000, "~1,000 单/月"),
    (2_000, "~600 单/月"),
    (5_000, "~300 单/月"),
    (10_000, "~150 单/月"),
    (20_000, "~80 单/月"),
    (50_000, "~30 单/月"),
    (100_000, "~15 单/月"),
    (200_000, "~5 单/月"),
]
