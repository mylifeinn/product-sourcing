from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, Request, Query, Form
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
async def dashboard(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=5, le=200),
):
    candidates = get_all_candidates(limit=500)
    
    # Enrich and score all for display
    enriched = []
    for c in candidates:
        c = enrich_candidate(c)
        c = score_candidate(c)
        c = run_compliance_check(c)
        
        gates = [
                    {"name": "gate_1", "label": "痛点关键词", "passed": c.gate_results.get("gate_1", False), "detail": c.gate_details.get("gate_1", "")},
                    {"name": "gate_2", "label": "趋势验证", "passed": c.gate_results.get("gate_2", False), "detail": c.gate_details.get("gate_2", "")},
                    {"name": "gate_3", "label": "毛利≥45%", "passed": c.gate_results.get("gate_3", False), "detail": c.gate_details.get("gate_3", "")},
                    {"name": "gate_4", "label": "轻便≤500g", "passed": c.gate_results.get("gate_4", False), "detail": c.gate_details.get("gate_4", "")},
                    {"name": "gate_5", "label": "质量达标", "passed": c.gate_results.get("gate_5", False), "detail": c.gate_details.get("gate_5", "")},
                    {"name": "gate_6", "label": "独特性", "passed": c.gate_results.get("gate_6", False), "detail": c.gate_details.get("gate_6", "")},
                    {"name": "gate_7", "label": "长青/季节", "passed": c.gate_results.get("gate_7", False), "detail": c.gate_details.get("gate_7", "")},
                    {"name": "gate_8", "label": "市场验证", "passed": c.gate_results.get("gate_8", False), "detail": c.gate_details.get("gate_8", "")},
                    {"name": "gate_9", "label": "客户价值", "passed": c.gate_results.get("gate_9", False), "detail": c.gate_details.get("gate_9", "")},
                ]
        
        passed_count = sum(1 for g in gates if g["passed"])
        
        enriched.append({
            "candidate": c,
            "gates": gates,
            "passed_count": passed_count,
            "total_gates": len(gates),
            "margin_color": "green" if c.estimated_margin_pct >= 45 else "orange" if c.estimated_margin_pct >= 30 else "red",
            "score_color": "success" if c.total_score >= 70 else "warning" if c.total_score >= 40 else "danger",
        })
    
    # Sort by score desc, then passed_count desc
    enriched.sort(key=lambda x: (x["candidate"].total_score, x["passed_count"]), reverse=True)
    
    stats = {
        "total": len(enriched),
        "passed_all": sum(1 for e in enriched if e["candidate"].passed_all_gates),
        "pending_review": sum(1 for e in enriched if e["candidate"].review_status == "pending"),
        "approved": sum(1 for e in enriched if e["candidate"].review_status == "approved"),
    }
    
    # Pagination
    total_pages = max(1, (len(enriched) + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    page_candidates = enriched[start:start + per_page]
    
    return render_template("dashboard.html", {
        "candidates": page_candidates,
        "all_count": len(enriched),
        "stats": stats,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
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
        {"name": "gate_1", "label": "痛点关键词 (≥3词, 搜索量≥500, KD≤30)", "passed": c.gate_results.get("gate_1", False), "detail": c.gate_details.get("gate_1", "")},
        {"name": "gate_2", "label": "趋势 (Trends YoY≥20% 或 TikTok 7d≥50%)", "passed": c.gate_results.get("gate_2", False), "detail": c.gate_details.get("gate_2", "")},
        {"name": "gate_3", "label": "毛利率 ≥45%", "passed": c.gate_results.get("gate_3", False), "detail": c.gate_details.get("gate_3", "")},
        {"name": "gate_4", "label": "轻便 (≤500g, ≤30×20×10cm, ePacket/4PX)", "passed": c.gate_results.get("gate_4", False), "detail": c.gate_details.get("gate_4", "")},
        {"name": "gate_5", "label": "质量 (供应商≥4.7, 退款≤3%, 有实拍)", "passed": c.gate_results.get("gate_5", False), "detail": c.gate_details.get("gate_5", "")},
        {"name": "gate_6", "label": "独特性 (Amazon/TEMU/SHEIN前3页无同款)", "passed": c.gate_results.get("gate_6", False), "detail": c.gate_details.get("gate_6", "")},
        {"name": "gate_7", "label": "长青/季节 (长青波动≤30% 或 季节旺季≥90天)", "passed": c.gate_results.get("gate_7", False), "detail": c.gate_details.get("gate_7", "")},
        {"name": "gate_8", "label": "市场验证 (竞品90天销量≥50 或 评论≥200)", "passed": c.gate_results.get("gate_8", False), "detail": c.gate_details.get("gate_8", "")},
        {"name": "gate_9", "label": "客户价值 (AOV≥$60, 复购≤90天, LTV≥3单)", "passed": c.gate_results.get("gate_9", False), "detail": c.gate_details.get("gate_9", "")},
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
async def update_review(
    candidate_id: str,
    status: str = Form(...),
    notes: str = Form(""),
    page: int = Form(1),
    per_page: int = Form(20),
):
    from sourcing.database import update_review_status
    from fastapi.responses import RedirectResponse
    valid_statuses = ["pending", "approved", "rejected", "waived"]
    if status not in valid_statuses:
        return {"error": "Invalid status"}
    update_review_status(candidate_id, status, notes)
    # 提交后回到列表页(保持当前页和每页行数)
    return RedirectResponse(url=f"/?page={page}&per_page={per_page}", status_code=303)


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