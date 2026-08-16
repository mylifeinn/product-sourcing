"""按 ASIN 去重 candidates 表: 同一 ASIN 保留 score 最高者, 其余删除。

用法: .venv/bin/python scripts/dedup_db.py
"""
import re
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "candidates.db"

ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")


def extract_asin(urls_json: str) -> str:
    m = ASIN_RE.search(urls_json or "")
    return m.group(1) if m else ""


def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, competitor_urls, total_score, created_at FROM candidates ORDER BY total_score DESC, created_at DESC"
    ).fetchall()

    best_by_asin: dict[str, str] = {}  # asin -> best id
    no_asin_ids: list[str] = []
    for r in rows:
        asin = extract_asin(r["competitor_urls"])
        if asin:
            if asin not in best_by_asin:
                best_by_asin[asin] = r["id"]
        else:
            no_asin_ids.append(r["id"])

    keep_ids = set(best_by_asin.values()) | set(no_asin_ids)
    delete_ids = [r["id"] for r in rows if r["id"] not in keep_ids]

    print(f"总数: {len(rows)}")
    print(f"有 ASIN 唯一产品: {len(best_by_asin)}")
    print(f"无 ASIN(保留): {len(no_asin_ids)}")
    print(f"将删除重复: {len(delete_ids)}")

    if delete_ids:
        con.executemany("DELETE FROM candidates WHERE id = ?", [(i,) for i in delete_ids])
        con.commit()
        print(f"已删除 {len(delete_ids)} 条重复")

    remaining = con.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    print(f"剩余: {remaining}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
