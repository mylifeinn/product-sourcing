from __future__ import annotations

from typing import List
from difflib import SequenceMatcher
from sourcing.models import ProductCandidate
from sourcing.config import get_config


def similarity(a: str, b: str) -> float:
    """Calculate string similarity ratio"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedup_candidates(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
    """按产品标题相似度去重(同一产品在不同 listing 的重复)。

    修复: 之前按 niche + pain_point_keywords 去重, 同 niche 候选的关键词相同,
    导致所有产品被合并成 1 个。现在按标题相似度(≥0.85)去重, 保留分数高的。
    """
    config = get_config()
    threshold = config.dedup.similarity_threshold

    unique = []
    for candidate in candidates:
        if not candidate.title.strip():
            continue
        is_dup = False
        for existing in unique:
            if similarity(candidate.title, existing.title) >= threshold:
                # Keep the one with higher score
                if candidate.total_score > existing.total_score:
                    unique.remove(existing)
                    unique.append(candidate)
                is_dup = True
                break

        if not is_dup:
            unique.append(candidate)

    return unique