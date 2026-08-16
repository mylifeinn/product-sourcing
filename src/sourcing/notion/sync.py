from __future__ import annotations

import os
from typing import List, Optional, Dict, Any
from notion_client import Client
from notion_client.errors import APIResponseError
from sourcing.models import ProductCandidate
from sourcing.config import get_settings
from sourcing.database import get_candidates_by_status


class NotionSync:
    """Notion integration for candidate review board"""
    
    def __init__(self):
        settings = get_settings()
        self.token = settings.notion_token
        self.db_id = settings.notion_db_id
        self.client = None
        
        # 只要 token 存在就初始化 client(db_id 可能尚未建库, 建库后再补)
        if self.token:
            self.client = Client(auth=self.token)
    
    def test_connection(self) -> bool:
        """Test Notion API connection"""
        if not self.client:
            return False
        try:
            self.client.databases.retrieve(database_id=self.db_id)
            return True
        except Exception:
            return False
    
    def upsert_candidate(self, candidate: ProductCandidate) -> bool:
        """Create or update a candidate page in Notion"""
        if not self.client:
            return False
        
        props = candidate.to_notion_properties()
        
        try:
            # Check if page already exists (by Candidate ID)
            existing = self._find_page_by_id(candidate.id)
            if existing:
                self.client.pages.update(page_id=existing["id"], properties=props)
            else:
                self.client.pages.create(
                    parent={"database_id": self.db_id},
                    properties=props
                )
            return True
        except APIResponseError as e:
            print(f"Notion API error: {e}")
            return False
    
    def _find_page_by_id(self, candidate_id: str) -> Optional[Dict]:
        """Find Notion page by Candidate ID property"""
        if not self.client:
            return None
        try:
            response = self.client.databases.query(
                database_id=self.db_id,
                filter={
                    "property": "Candidate ID",
                    "title": {"equals": candidate_id}
                }
            )
            results = response.get("results", [])
            return results[0] if results else None
        except Exception:
            return None
    
    def get_approved_candidates(self) -> List[ProductCandidate]:
        """Get candidates with 'approved' review status from Notion"""
        if not self.client:
            return []
        
        try:
            response = self.client.databases.query(
                database_id=self.db_id,
                filter={
                    "property": "审核状态",
                    "select": {"equals": "通过"}
                }
            )
            # Convert Notion pages back to candidates (would need reverse mapping)
            # For now, return from local DB
            return get_candidates_by_status("approved")
        except Exception:
            return []
    
    def bulk_upsert(self, candidates: List[ProductCandidate]) -> int:
        """Bulk upsert candidates to Notion (按 score 降序自动编序号)"""
        # 按 score 降序排, 同分按完整度, 序号 1,2,3...
        ordered = sorted(
            candidates,
            key=lambda c: (c.total_score, c.data_completeness_pct),
            reverse=True,
        )
        for i, c in enumerate(ordered, 1):
            c.rank = i
        success = 0
        for candidate in ordered:
            if self.upsert_candidate(candidate):
                success += 1
        return success

    def clear_database(self) -> int:
        """清空数据库所有页面(物理删除, 供重同步去重)。返回删除数。

        注意: Notion API 无批量删, 逐条 archive。数据量几百时 OK, 上千会慢。
        """
        if not self.client or not self.db_id:
            return 0
        import httpx

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": "2022-06-28",
        }
        deleted = 0
        try:
            while True:
                resp = httpx.post(
                    f"https://api.notion.com/v1/databases/{self.db_id}/query",
                    headers=headers,
                    json={"page_size": 100},
                    timeout=30,
                )
                rows = resp.json().get("results", [])
                if not rows:
                    break
                for row in rows:
                    r = httpx.patch(
                        f"https://api.notion.com/v1/pages/{row['id']}",
                        headers={**headers, "Content-Type": "application/json"},
                        json={"archived": True},
                        timeout=30,
                    )
                    if r.status_code == 200:
                        deleted += 1
                if len(rows) < 100:
                    break
        except Exception as e:
            print(f"clear_database error: {e}")
        return deleted

    def sync_all(self, candidates: List[ProductCandidate], clear_first: bool = False) -> dict:
        """清空(可选) + 全量同步。返回统计。"""
        result = {"cleared": 0, "synced": 0, "total": len(candidates)}
        if clear_first:
            result["cleared"] = self.clear_database()
        result["synced"] = self.bulk_upsert(candidates)
        return result

    @staticmethod
    def dedup_by_asin(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
        """按 ASIN 去重: 同一 ASIN 保留 score 最高者(同分保留最新)。"""
        import re as _re

        best: dict[str, ProductCandidate] = {}
        ordered = sorted(
            candidates,
            key=lambda c: (c.total_score, c.data_completeness_pct, c.created_at),
            reverse=True,
        )
        for c in ordered:
            asin = ""
            if c.competitor_urls:
                m = _re.search(r"/dp/([A-Z0-9]{10})", c.competitor_urls[0])
                asin = m.group(1) if m else ""
            if asin:
                if asin not in best:
                    best[asin] = c
            else:
                best.setdefault(c.id, c)
        return list(best.values())

    def sync_all_deduped(self, clear_first: bool = False) -> dict:
        """从本地 DB 读全部候选 → ASIN 去重 → 清空(可选) → 同步。一步到位。"""
        from sourcing.database import get_all_candidates

        candidates = self.dedup_by_asin(get_all_candidates(limit=2000))
        return self.sync_all(candidates, clear_first=clear_first)

    # ------------------------------------------------------------------
    # 自动创建审核数据库(用户只需提供 token + 父页面, 不用手动建库)
    # ------------------------------------------------------------------
    @staticmethod
    def build_database_schema() -> dict:
        """审核数据库完整属性 schema(与 models.to_notion_properties 对齐)"""
        return {
            "Candidate ID": {"title": {}},
            "序号": {"number": {}},
            "产品标题": {"rich_text": {}},
            "细分品类": {"select": {"options": []}},
            "痛点关键词": {"multi_select": {"options": []}},
            "Gate 1 痛点词": {"checkbox": {}},
            "Gate 2 趋势": {"checkbox": {}},
            "Gate 3 毛利": {"checkbox": {}},
            "Gate 4 轻便": {"checkbox": {}},
            "Gate 5 质量": {"checkbox": {}},
            "Gate 6 独特性": {"checkbox": {}},
            "Gate 7 长青性": {"checkbox": {}},
            "Gate 8 验证": {"checkbox": {}},
            "Gate 9 客户价值": {"checkbox": {}},
            "专利风险": {"select": {"options": [{"name": "无"}, {"name": "低"}, {"name": "高"}]}},
            "商标风险": {"select": {"options": [{"name": "无"}, {"name": "低"}, {"name": "高"}]}},
            "总分": {"number": {}},
            "通过状态": {"select": {"options": [{"name": "通过"}, {"name": "拒绝"}, {"name": "人工豁免"}]}},
            "批发价": {"number": {}},
            "建议零售价": {"number": {}},
            "毛利率%": {"number": {}},
            "重量(g)": {"number": {}},
            "尺寸(cm)": {"rich_text": {}},
            "物流渠道": {"rich_text": {}},
            "供应商链接": {"url": {}},
            "供应商联系人": {"rich_text": {}},
            "竞品验证链接": {"url": {}},
            "审核状态": {"select": {"options": [{"name": "待审"}, {"name": "通过"}, {"name": "拒绝"}, {"name": "豁免通过"}]}},
            "审核备注": {"rich_text": {}},
            "健康度": {"select": {"options": [{"name": "健康"}, {"name": "需关注"}, {"name": "待优化"}]}},
        }

    def create_database(self, parent_page_id: str) -> Optional[str]:
        """在父页面下创建审核数据库, 返回 database_id。

        ⚠️ 不用 notion_client.databases.create: 该库对 properties 序列化有 bug
        (中文属性名 + select options 会静默丢弃, 只留默认 Name)。改用 httpx 直连 API。
        """
        if not self.client:
            return None
        import httpx

        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"text": {"content": "选品审核板"}}],
            "properties": self.build_database_schema(),
        }
        try:
            resp = httpx.post(
                "https://api.notion.com/v1/databases",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Notion-Version": "2022-06-28",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=30,
            )
            data = resp.json()
            if resp.status_code == 200:
                return data.get("id")
            print(f"Notion create database error: {data.get('message', data)}")
            return None
        except Exception as e:
            print(f"Notion create database error: {e}")
            return None

    @staticmethod
    def parse_page_id(page_url_or_id: str) -> str:
        """从 Notion 页面 URL 或裸 ID 提取 page_id。

        支持:
        - https://www.notion.so/MyPage-1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
        - https://www.notion.so/workspace/1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d?v=xxx
        - 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d (裸 ID)
        """
        import re as _re
        s = (page_url_or_id or "").strip()
        # 匹配 32 位 hex (允许带连字符)
        m = _re.search(r'([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', s, _re.I)
        if m:
            return m.group(1).replace("-", "")
        return ""