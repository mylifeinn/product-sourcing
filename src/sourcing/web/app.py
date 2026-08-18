from __future__ import annotations

import os
import asyncio
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
from sourcing.config import load_config, get_config
from sourcing.pipeline.fetch import fetch_niche


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
    config = get_config()
    seed_niches = list(config.seed_niches)
    
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
        "seed_niches": seed_niches,
        "data_sources": config.data_sources,
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


@app.post("/fetch")
async def fetch_products(
    request: Request,
    niche: str = Form(...),
    source: str = Form("public"),
    limit: int = Form(20),
):
    """Trigger product fetching from dashboard"""
    # Validate source against config
    config = get_config()
    valid_sources = [ds["name"] for ds in config.data_sources if ds.get("enabled", True)]
    if source not in valid_sources:
        return {"success": False, "error": f"无效的数据源: {source}，支持: {', '.join(valid_sources)}"}

    import time
    import uuid
    start_time = time.time()
    task_id = str(uuid.uuid4())[:8]

    try:
        print(f"[FETCH {task_id}] Starting fetch for niche='{niche}', source='{source}', limit={limit}")

        # Validate inputs
        if not niche or not niche.strip():
            return {"success": False, "error": "品类关键词不能为空"}

        limit = max(1, min(limit, 50))  # Clamp to 1-50
        
        # Step 1: Fetch raw candidates
        print(f"[FETCH {task_id}] Step 1/5: Fetching raw candidates...")
        candidates = await fetch_niche(niche.strip(), source, limit)
        fetch_time = time.time() - start_time
        print(f"[FETCH {task_id}] Step 1 done: got {len(candidates)} raw candidates in {fetch_time:.1f}s")
        
        if not candidates:
            return {"success": True, "count": 0, "message": f"未找到 '{niche}' 的候选产品（可能被反爬或无结果），建议尝试其他关键词或稍后再试", "task_id": task_id}
        
        # Step 2: Enrich
        print(f"[FETCH {task_id}] Step 2/5: Enriching candidates...")
        enrich_start = time.time()
        from sourcing.pipeline.enrich import enrich_candidates
        candidates = enrich_candidates(candidates)
        print(f"[FETCH {task_id}] Step 2 done in {time.time() - enrich_start:.1f}s")
        
        # Step 3: Score
        print(f"[FETCH {task_id}] Step 3/5: Scoring candidates...")
        score_start = time.time()
        from sourcing.pipeline.score import score_candidates
        candidates = score_candidates(candidates)
        print(f"[FETCH {task_id}] Step 3 done in {time.time() - score_start:.1f}s")
        
        # Step 4: Dedup
        print(f"[FETCH {task_id}] Step 4/5: Deduplicating...")
        dedup_start = time.time()
        from sourcing.pipeline.dedup import dedup_candidates
        candidates = dedup_candidates(candidates)
        print(f"[FETCH {task_id}] Step 4 done in {time.time() - dedup_start:.1f}s")
        
        # Step 5: Compliance
        print(f"[FETCH {task_id}] Step 5/5: Compliance check...")
        compliance_start = time.time()
        from sourcing.compliance.check import run_compliance_checks
        candidates = run_compliance_checks(candidates)
        print(f"[FETCH {task_id}] Step 5 done in {time.time() - compliance_start:.1f}s")
        
        # Persist to database
        print(f"[FETCH {task_id}] Persisting {len(candidates)} candidates to database...")
        from sourcing.database import upsert_candidate
        for c in candidates:
            upsert_candidate(c)
        
        # Export to Notion
        print(f"[FETCH {task_id}] Syncing to Notion...")
        from sourcing.notion.sync import NotionSync
        notion = NotionSync()
        if notion.client:
            notion.bulk_upsert(candidates)
            print(f"[FETCH {task_id}] Notion sync done")
        else:
            print(f"[FETCH {task_id}] Notion not configured, skipping")
        
        total_time = time.time() - start_time
        passed = sum(1 for c in candidates if c.passed_all_gates)
        msg = f"✅ 完成！耗时 {total_time:.1f}s：抓取 {len(candidates)} 个候选，{passed} 个全通过 9 门槛"
        print(f"[FETCH {task_id}] {msg}")
        
        return {"success": True, "count": len(candidates), "message": msg, "passed_all": passed, "elapsed_sec": round(total_time, 1), "task_id": task_id}
        
    except asyncio.TimeoutError:
        return {"success": False, "error": "请求超时（>120s），建议减少抓取数量或稍后重试", "task_id": task_id}
    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"[FETCH {task_id}] ERROR: {error_msg}")
        print(traceback.format_exc())
        return {"success": False, "error": error_msg, "task_id": task_id}


# Progress tracking for long-running fetches
_fetch_progress = {}

@app.post("/fetch/start")
async def fetch_start(
    request: Request,
    niche: str = Form(...),
    source: str = Form("public"),
    limit: int = Form(20),
):
    """Start async fetch, return task_id for polling"""
    # Validate source against config
    config = get_config()
    valid_sources = [ds["name"] for ds in config.data_sources if ds.get("enabled", True)]
    if source not in valid_sources:
        return {"error": f"无效的数据源: {source}，支持: {', '.join(valid_sources)}"}

    import uuid
    task_id = str(uuid.uuid4())
    _fetch_progress[task_id] = {"status": "starting", "step": 0, "message": "初始化...", "result": None}

    # Run in background
    asyncio.create_task(_run_fetch_task(task_id, niche, source, limit))

    return {"task_id": task_id}


async def _run_fetch_task(task_id: str, niche: str, source: str, limit: int):
    import time
    start_time = time.time()

    def update(step: int, message: str):
        _fetch_progress[task_id] = {"status": "running", "step": step, "message": message, "result": None}

    try:
        update(0, "验证参数...")
        if not niche or not niche.strip():
            _fetch_progress[task_id] = {"status": "error", "step": 0, "message": "品类关键词不能为空", "result": None}
            return
        config = get_config()
        valid_sources = [ds["name"] for ds in config.data_sources if ds.get("enabled", True)]
        if source not in valid_sources:
            _fetch_progress[task_id] = {"status": "error", "step": 0, "message": f"无效数据源: {source}", "result": None}
            return
        limit = max(1, min(limit, 50))
        
        update(1, "步骤 1/5: 抓取原始候选...")
        candidates = await fetch_niche(niche.strip(), source, limit)
        if not candidates:
            _fetch_progress[task_id] = {"status": "done", "step": 5, "message": f"未找到 '{niche}' 的候选产品", "result": {"success": True, "count": 0, "passed_all": 0, "elapsed_sec": round(time.time() - start_time, 1)}}
            return
        
        update(2, f"步骤 2/5: 富化 {len(candidates)} 个候选...")
        from sourcing.pipeline.enrich import enrich_candidates
        candidates = enrich_candidates(candidates)
        
        update(3, "步骤 3/5: 评分...")
        from sourcing.pipeline.score import score_candidates
        candidates = score_candidates(candidates)
        
        update(4, "步骤 4/5: 去重...")
        from sourcing.pipeline.dedup import dedup_candidates
        candidates = dedup_candidates(candidates)
        
        update(5, "步骤 5/5: 合规检查...")
        from sourcing.compliance.check import run_compliance_checks
        candidates = run_compliance_checks(candidates)
        
        update(5, "落库...")
        from sourcing.database import upsert_candidate
        for c in candidates:
            upsert_candidate(c)
        
        update(5, "同步 Notion...")
        from sourcing.notion.sync import NotionSync
        notion = NotionSync()
        if notion.client:
            notion.bulk_upsert(candidates)
        
        total_time = time.time() - start_time
        passed = sum(1 for c in candidates if c.passed_all_gates)
        msg = f"✅ 完成！耗时 {total_time:.1f}s：抓取 {len(candidates)} 个候选，{passed} 个全通过 9 门槛"
        _fetch_progress[task_id] = {"status": "done", "step": 5, "message": msg, "result": {"success": True, "count": len(candidates), "passed_all": passed, "elapsed_sec": round(total_time, 1), "message": msg}}
        
    except Exception as e:
        import traceback
        error_msg = f"{type(e).__name__}: {str(e)}"
        print(f"[FETCH {task_id}] ERROR: {error_msg}")
        print(traceback.format_exc())
        _fetch_progress[task_id] = {"status": "error", "step": 0, "message": error_msg, "result": None}

@app.get("/fetch/progress/{task_id}")
async def fetch_progress(task_id: str):
    """Poll fetch progress"""
    if task_id not in _fetch_progress:
        return {"status": "not_found", "message": "任务不存在或已过期"}
    return _fetch_progress[task_id]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8085)