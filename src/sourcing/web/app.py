from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader, select_autoescape

from sourcing.database import get_all_candidates, get_candidate, init_db, update_review_status
from sourcing.pipeline.score import score_candidate
from sourcing.pipeline.enrich import enrich_candidate
from sourcing.compliance.check import run_compliance_check
from sourcing.config import load_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()
    init_db()
    yield


app = FastAPI(title="Product Sourcing Dashboard", lifespan=lifespan)

BASE_DIR = Path(__file__).parent.parent.parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Use raw Jinja2 Environment instead of Starlette's wrapper
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,  # Disable caching
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def render_template(template_name: str, context: dict) -> HTMLResponse:
    """Render template with raw Jinja2"""
    template = jinja_env.get_template(template_name)
    html = template.render(**context)
    return HTMLResponse(content=html)


class GateResult(BaseModel):
    name: str
    label: str
    passed: bool
    detail: str = ""


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    candidates = get_all_candidates(limit=200)
    
    # Enrich and score all for display
    enriched = []
    for c in candidates:
        c = enrich_candidate(c)
        c = score_candidate(c)
        c = run_compliance_check(c)
        
        gates = [
                    {"name": "gate_1", "label": "痛点关键词", "passed": c.gate_results.get("gate_1", False), "detail": f"{len([k for k in c.longtail_keywords if k.get('volume',0)>=500 and k.get('kd',99)<=30])} 个合格词"},
                    {"name": "gate_2", "label": "趋势验证", "passed": c.gate_results.get("gate_2", False), "detail": f"Trends YoY: {c.google_trends_yoy_pct:.1f}%"},
                    {"name": "gate_3", "label": "毛利≥45%", "passed": c.gate_results.get("gate_3", False), "detail": f"{c.estimated_margin_pct:.1f}%"},
                    {"name": "gate_4", "label": "轻便≤500g", "passed": c.gate_results.get("gate_4", False), "detail": f"{c.weight_g:.0f}g, {c.dimensions_cm[0]:.0f}×{c.dimensions_cm[1]:.0f}×{c.dimensions_cm[2]:.0f}cm"},
                    {"name": "gate_5", "label": "质量达标", "passed": c.gate_results.get("gate_5", False), "detail": f"供应商评分 {c.supplier_rating:.1f}, 退款率 {c.refund_rate_pct:.1f}%"},
                    {"name": "gate_6", "label": "独特性", "passed": c.gate_results.get("gate_6", False), "detail": "无同款竞品" if c.uniqueness_passed else "发现同款"},
                    {"name": "gate_7", "label": "长青/季节", "passed": c.gate_results.get("gate_7", False), "detail": "长青" if c.is_evergreen else f"季节性 {c.seasonal_peak_window_days}天"},
                    {"name": "gate_8", "label": "市场验证", "passed": c.gate_results.get("gate_8", False), "detail": f"竞品90天销量 {c.competitor_sales_90d}, 评论 {c.competitor_reviews}"},
                    {"name": "gate_9", "label": "客户价值", "passed": c.gate_results.get("gate_9", False), "detail": f"AOV ${c.estimated_aov_usd:.0f}, LTV {c.estimated_ltv_orders:.1f}单"},
                ]
        
        passed_count = sum(1 for g in gates if g["passed"])
        
        enriched.append({
            "candidate": c,
            "gates": gates,
            "passed_count": passed_count,
            "total_gates": len(gates),
            "margin_color": "green" if c.estimated_margin_pct >= 45 else "orange" if c.estimated_margin_pct >= 30 else "red",
        })
    
    # Sort by score desc, then passed_count desc
    enriched.sort(key=lambda x: (x["candidate"].total_score, x["passed_count"]), reverse=True)
    
    stats = {
        "total": len(enriched),
        "passed_all": sum(1 for e in enriched if e["candidate"].passed_all_gates),
        "pending_review": sum(1 for e in enriched if e["candidate"].review_status == "pending"),
        "approved": sum(1 for e in enriched if e["candidate"].review_status == "approved"),
    }
    
    return render_template("dashboard.html", {
        "candidates": enriched,
        "stats": stats,
    })


@app.get("/candidate/{candidate_id}", response_class=HTMLResponse)
async def candidate_detail(request: Request, candidate_id: str):
    c = get_candidate(candidate_id)
    if not c:
        return HTMLResponse("Candidate not found", status_code=404)
    
    c = enrich_candidate(c)
    c = score_candidate(c)
    c = run_compliance_check(c)
    
    gates = [
        {"name": "gate_1", "label": "痛点关键词 (≥3词, 搜索量≥500, KD≤30)", "passed": c.gate_results.get("gate_1", False), "detail": f"{len(c.longtail_keywords)} 个长尾词"},
        {"name": "gate_2", "label": "趋势 (Trends YoY≥20% 或 TikTok 7d≥50%)", "passed": c.gate_results.get("gate_2", False), "detail": f"Google Trends YoY: {c.google_trends_yoy_pct:.1f}%, TikTok代理: {c.tiktok_hashtag_growth_pct:.1f}%"},
        {"name": "gate_3", "label": "毛利率 ≥45%", "passed": c.gate_results.get("gate_3", False), "detail": f"{c.estimated_margin_pct:.1f}% (零售${c.estimated_retail_price_usd:.2f} - 批发${c.wholesale_price_usd:.2f} - 运费${c.estimated_shipping_usd:.2f})"},
        {"name": "gate_4", "label": "轻便 (≤500g, ≤30×20×10cm, ePacket/4PX)", "passed": c.gate_results.get("gate_4", False), "detail": f"{c.weight_g:.0f}g, {c.dimensions_cm[0]:.0f}×{c.dimensions_cm[1]:.0f}×{c.dimensions_cm[2]:.0f}cm, 渠道: {c.shipping_channel}"},
        {"name": "gate_5", "label": "质量 (供应商≥4.7, 退款≤3%, 有实拍)", "passed": c.gate_results.get("gate_5", False), "detail": f"评分 {c.supplier_rating:.1f}, 退款 {c.refund_rate_pct:.1f}%, 实拍: {'是' if c.has_actual_photos else '否'}"},
        {"name": "gate_6", "label": "独特性 (Amazon/TEMU/SHEIN前3页无同款)", "passed": c.gate_results.get("gate_6", False), "detail": "通过" if c.uniqueness_passed else "发现同款竞品"},
        {"name": "gate_7", "label": "长青/季节 (长青波动≤30% 或 季节旺季≥90天)", "passed": c.gate_results.get("gate_7", False), "detail": "长青" if c.is_evergreen else f"季节性: 旺季 {c.seasonal_peak_window_days}天, 提前 {c.prep_lead_time_days}天备货"},
        {"name": "gate_8", "label": "市场验证 (竞品90天销量≥50 或 评论≥200)", "passed": c.gate_results.get("gate_8", False), "detail": f"竞品销量 {c.competitor_sales_90d}, 评论 {c.competitor_reviews}"},
        {"name": "gate_9", "label": "客户价值 (AOV≥$60, 复购≤90天, LTV≥3单)", "passed": c.gate_results.get("gate_9", False), "detail": f"AOV ${c.estimated_aov_usd:.0f}, 复购周期 {c.estimated_repurchase_cycle_days}天, LTV {c.estimated_ltv_orders:.1f}单"},
    ]
    
    # Cost estimate details if available
    cost_est = getattr(c, 'cost_estimate', None)
    
    return render_template("detail.html", {
        "candidate": c,
        "gates": gates,
        "passed_count": sum(1 for g in gates if g["passed"]),
        "total_gates": len(gates),
        "cost_est": cost_est,
    })


@app.post("/candidate/{candidate_id}/review")
async def update_review(candidate_id: str, status: str = Query(...), notes: str = Query("")):
    from sourcing.database import update_review_status
    valid_statuses = ["pending", "approved", "rejected", "waived"]
    if status not in valid_statuses:
        return {"error": "Invalid status"}
    update_review_status(candidate_id, status, notes)
    return {"ok": True, "status": status}


@app.get("/api/candidates")
async def api_candidates(status: Optional[str] = None, limit: int = 100):
    from sourcing.database import get_candidates_by_status, get_all_candidates
    if status:
        candidates = get_candidates_by_status(status, limit)
    else:
        candidates = get_all_candidates(limit)
    return [{"id": c.id, "title": c.title, "niche": c.niche, "score": c.total_score, "passed": c.passed_all_gates, "status": c.review_status} for c in candidates]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8085)