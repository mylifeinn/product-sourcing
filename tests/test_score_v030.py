"""验证 v0.3.0 新评分逻辑(纯内存, 不爬网)"""
import sys
sys.path.insert(0, "src")

from sourcing.models import ProductCandidate
from sourcing.pipeline.score import score_candidate


def mk(**kw):
    c = ProductCandidate()
    c.longtail_keywords = [
        {"keyword": "neck massager for pain", "volume": 0, "kd": 0,
         "volume_provenance": "MISSING", "kd_provenance": "MISSING"},
        {"keyword": "neck massager for travel", "volume": 0, "kd": 0,
         "volume_provenance": "MISSING", "kd_provenance": "MISSING"},
        {"keyword": "best neck massager 2026", "volume": 0, "kd": 0,
         "volume_provenance": "MISSING", "kd_provenance": "MISSING"},
    ]
    c.amazon_result_count = 2000   # REAL volume 代理
    c.google_trends_yoy_pct = -36.6  # 头词下跌
    c.estimated_aov_usd = 99.0
    c.estimated_retail_price_usd = 99.0
    c.wholesale_price_usd = 25.0
    c.estimated_shipping_usd = 5.0
    c.estimated_margin_pct = 60.0
    c.weight_g = 220
    c.dimensions_cm = (15, 10, 5)
    c.amazon_rating = 4.3
    c.competitor_sales_90d = 1500
    c.competitor_reviews = 350
    c.amazon_duplicate_count = 2
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def show(name, c):
    g = c.gate_results
    print(f"\n=== {name} ===")
    print(f"  score={c.total_score} pass={c.passed_all_gates} review={c.needs_manual_review}")
    for k in ["gate_1", "gate_2", "gate_6"]:
        print(f"  {k}={g.get(k)}  {c.gate_details.get(k, '')[:90]}")


# 1. 头词跌 + volume 代理 + 上升词 → Gate1 应过, Gate2 长尾通道过, Gate6 过
c1 = mk()
c1.longtail_keywords += [
    {"keyword": "heated neck massager", "volume": 0, "kd": 0,
     "volume_provenance": "MISSING", "kd_provenance": "MISSING",
     "trending_provenance": "REAL", "trending_value": 250},
    {"keyword": "shockwave neck massager", "volume": 0, "kd": 0,
     "volume_provenance": "MISSING", "kd_provenance": "MISSING",
     "trending_provenance": "REAL", "trending_value": 150},
]
show("头词跌+volume代理+2个上升词", score_candidate(c1))
assert c1.gate_results["gate_1"] is True, "Gate1 volume 代理应通过"
assert c1.gate_results["gate_2"] is True, "Gate2 上升词通道应通过"
assert c1.gate_results["gate_6"] is True, "Gate6 同款2≤3 应通过"

# 2. 头词跌 + 无上升词 + 无 BSR → Gate2 明确不通过
c2 = mk()
show("头词跌+无上升词", score_candidate(c2))
assert c2.gate_results["gate_2"] is False, "Gate2 明确下跌应不通过"

# 3. 无趋势数据(YoY=0 无上升词) → Gate2 None
c3 = mk()
c3.google_trends_yoy_pct = 0.0
show("趋势数据缺失", score_candidate(c3))
assert c3.gate_results["gate_2"] is None, "Gate2 数据缺失应为 None"

# 4. Gate6: 同款 5 个 → 不通过; 未检测(-1) → None
c4 = mk()
c4.amazon_duplicate_count = 5
score_candidate(c4)
assert c4.gate_results["gate_6"] is False, "Gate6 同款5>3 应不通过"
c5 = mk()
c5.amazon_duplicate_count = -1
score_candidate(c5)
assert c5.gate_results["gate_6"] is None, "Gate6 未检测应为 None"

# 5. Gate1: result_count 太小 → 不通过; 未抓到(0) → 走宽松
c6 = mk()
c6.amazon_result_count = 60
score_candidate(c6)
assert c6.gate_results["gate_1"] is False, "Gate1 result_count<500 应不通过"

# 6. 完整候选: 全部9门通过的场景
c7 = mk()
c7.google_trends_yoy_pct = 45.0
c7.amazon_duplicate_count = 0
c7.competitor_sales_90d = 800
c7.amazon_rating = 4.6
score_candidate(c7)
show("全通过场景", c7)
assert c7.passed_all_gates is True, "应全通过"

print("\n✅ 全部 8 个断言通过")
