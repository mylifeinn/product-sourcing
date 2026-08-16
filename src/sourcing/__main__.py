from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from sourcing.config import get_config, get_settings, load_config
from sourcing.database import init_db, upsert_candidate, get_candidate, get_candidates_by_status, get_all_candidates
from sourcing.pipeline.fetch import fetch_niche
from sourcing.pipeline.enrich import enrich_candidates, enrich_candidate
from sourcing.pipeline.score import score_candidates, score_candidate
from sourcing.pipeline.dedup import dedup_candidates
from sourcing.compliance.check import run_compliance_checks, run_compliance_check, init_compliance_cache
from sourcing.seo.template import render_seo_meta
from sourcing.notion.sync import NotionSync
from sourcing.shopify.sync import ShopifySync
from sourcing.health.check import run_healthcheck, send_alerts
from sourcing.models import ProductCandidate

app = typer.Typer(name="sourcing", help="Product Sourcing Pipeline for SEO/GEO Shopify Stores")
console = Console()


@app.callback()
def callback():
    """Initialize config and database"""
    load_config()
    init_db()
    init_compliance_cache()


@app.command()
def fetch(
    niche: str = typer.Argument(..., help="Niche/keyword to search for"),
    source: str = typer.Option("public", "--source", "-s", help="Data source: public(真实公开数据, 默认), real, mock(仅测试)"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max products to fetch"),
):
    """Fetch product candidates for a niche"""
    console.print(f"[bold blue]Fetching[/bold blue] {limit} products for niche: [cyan]{niche}[/cyan] from [cyan]{source}[/cyan]")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching...", total=None)
        candidates = asyncio.run(fetch_niche(niche, source, limit))
        progress.update(task, description=f"Fetched {len(candidates)} raw candidates")
    
    if not candidates:
        console.print("[yellow]No candidates found[/yellow]")
        return
    
    # Enrich, score, dedup, compliance
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Enriching...", total=None)
        candidates = enrich_candidates(candidates)
        progress.update(task, description="Scoring...")
        candidates = score_candidates(candidates)
        progress.update(task, description="Deduplicating...")
        candidates = dedup_candidates(candidates)
        progress.update(task, description="Compliance check...")
        candidates = run_compliance_checks(candidates)
    
    # Persist to database
    for c in candidates:
        upsert_candidate(c)
    
    # Export to Notion
    notion = NotionSync()
    if notion.client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Syncing to Notion...", total=None)
            synced = notion.bulk_upsert(candidates)
            progress.update(task, description=f"Synced {synced}/{len(candidates)} to Notion")
    
    # Display results
    table = Table(title=f"Candidates for {niche}")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Margin%", justify="right", style="yellow")
    table.add_column("Weight(g)", justify="right")
    table.add_column("Gates", style="magenta")
    table.add_column("完整度", justify="right", style="blue")
    table.add_column("需复核", style="red")
    table.add_column("Status", style="blue")

    for c in candidates:
        gates_passed = sum(1 for v in c.gate_results.values() if v is True)
        gates_str = f"{gates_passed}/9 {'✅' if c.passed_all_gates else '❌'}"
        table.add_row(
            c.id,
            c.title[:50] + "..." if len(c.title) > 50 else c.title,
            str(c.total_score),
            f"{c.estimated_margin_pct:.1f}%",
            f"{c.weight_g:.0f}",
            gates_str,
            f"{c.data_completeness_pct}%",
            "⚠️" if c.needs_manual_review else "",
            c.review_status,
        )
    
    console.print(table)
    console.print(f"\n[green]Done![/green] {len(candidates)} candidates saved to database and Notion.")


@app.command()
def score(
    candidate_id: str = typer.Argument(..., help="Candidate ID to re-score"),
):
    """Re-score a specific candidate"""
    candidate = get_candidate(candidate_id)
    if not candidate:
        console.print(f"[red]Candidate {candidate_id} not found[/red]")
        return
    
    candidate = enrich_candidate(candidate)
    candidate = score_candidate(candidate)
    candidate = run_compliance_check(candidate)
    upsert_candidate(candidate)
    
    console.print(f"[green]Re-scored {candidate_id}:[/green] Score={candidate.total_score}, Passed={candidate.passed_all_gates}")


@app.command()
def export(
    status: str = typer.Option("pending", "--status", "-s", help="Filter by review status"),
    format: str = typer.Option("csv", "--format", "-f", help="Export format: csv, json"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file path"),
):
    """Export candidates to CSV/JSON"""
    candidates = get_candidates_by_status(status, limit=1000)
    
    if format == "csv":
        import csv
        rows = []
        for c in candidates:
            rows.append({
                "id": c.id,
                "title": c.title,
                "niche": c.niche,
                "pain_points": ";".join(c.pain_point_keywords),
                "score": c.total_score,
                "passed": c.passed_all_gates,
                "margin_pct": c.estimated_margin_pct,
                "retail_price": c.estimated_retail_price_usd,
                "wholesale_price": c.wholesale_price_usd,
                "weight_g": c.weight_g,
                "patent_risk": c.patent_risk_level,
                "trademark_risk": c.trademark_risk_level,
                "review_status": c.review_status,
                "created_at": c.created_at.isoformat(),
            })
        
        if output:
            with open(output, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
                writer.writeheader()
                writer.writerows(rows)
            console.print(f"[green]Exported {len(rows)} candidates to {output}[/green]")
        else:
            console.print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        data = [c.__dict__ for c in candidates]
        if output:
            with open(output, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            console.print(f"[green]Exported {len(data)} candidates to {output}[/green]")
        else:
            console.print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


@app.command()
def push_shopify(
    ids: List[str] = typer.Argument(..., help="Candidate IDs to push to Shopify"),
    publish: bool = typer.Option(False, "--publish", "-p", help="Publish immediately (default: draft)"),
):
    """Push approved candidates to Shopify as draft products"""
    settings = get_settings()
    brand_config = {
        "vendor": "YourBrand",
        "voice": "",
        "usp_framework": "",
        "trust_anchors": "",
    }
    
    shopify_sync = ShopifySync()
    if not shopify_sync.test_connection():
        console.print("[red]Shopify connection failed. Check credentials.[/red]")
        return
    
    for cid in ids:
        candidate = get_candidate(cid)
        if not candidate:
            console.print(f"[yellow]Candidate {cid} not found, skipping[/yellow]")
            continue
        
        if candidate.review_status not in ["approved", "waived"]:
            console.print(f"[yellow]Candidate {cid} not approved (status: {candidate.review_status}), skipping[/yellow]")
            continue
        
        console.print(f"Creating draft product for {candidate.title}...")
        draft_id = shopify_sync.create_draft_product(candidate, brand_config)
        
        if draft_id:
            candidate.shopify_draft_id = draft_id
            upsert_candidate(candidate)
            console.print(f"[green]✓ Created draft product ID: {draft_id}[/green]")
            
            if publish:
                product_id = shopify_sync.publish_product(draft_id)
                if product_id:
                    candidate.shopify_product_id = product_id
                    candidate.published_at = datetime.now()
                    candidate.review_status = "published"
                    upsert_candidate(candidate)
                    console.print(f"[green]✓ Published product ID: {product_id}[/green]")
        else:
            console.print(f"[red]✗ Failed to create draft for {cid}[/red]")


@app.command()
def healthcheck(
    days: int = typer.Option(30, "--days", "-d", help="Check products published within N days"),
):
    """Run health check on published products"""
    console.print(f"[bold blue]Running health check for last {days} days...[/bold blue]")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Checking...", total=None)
        results = run_healthcheck(days)
        progress.update(task, description=f"Checked {len(results)} products")
    
    if not results:
        console.print("[yellow]No published products found in window[/yellow]")
        return
    
    table = Table(title="Product Health Check")
    table.add_column("Product", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Days", justify="right")
    table.add_column("Window", style="blue")
    table.add_column("Health", style="green")
    table.add_column("Alerts", style="red")
    
    for h in results:
        health_style = "green" if h["health_label"] == "健康" else "yellow" if h["health_label"] == "需关注" else "red"
        alerts_str = "; ".join(h["alerts"]) if h["alerts"] else "—"
        table.add_row(
            h["candidate_title"][:40],
            h["candidate_id"],
            str(h["days_since_publish"]),
            h["window"],
            f"[{health_style}]{h['health_label']}[/{health_style}]",
            alerts_str,
        )
    
    console.print(table)
    send_alerts(results)


@app.command()
def test_notion():
    """Test Notion connection"""
    notion = NotionSync()
    if notion.test_connection():
        console.print("[green]✓ Notion connection successful[/green]")
    else:
        console.print("[red]✗ Notion connection failed[/red]")


@app.command()
def test_shopify():
    """Test Shopify connection"""
    shopify_sync = ShopifySync()
    if shopify_sync.test_connection():
        console.print("[green]✓ Shopify connection successful[/green]")
    else:
        console.print("[red]✗ Shopify connection failed[/red]")


@app.command()
def init():
    """Initialize database and cache"""
    init_db()
    init_compliance_cache()
    console.print("[green]✓ Database and compliance cache initialized[/green]")


if __name__ == "__main__":
    app()