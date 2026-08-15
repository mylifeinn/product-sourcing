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
        
        if self.token and self.db_id:
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
        """Bulk upsert candidates to Notion"""
        success = 0
        for candidate in candidates:
            if self.upsert_candidate(candidate):
                success += 1
        return success