from __future__ import annotations

from typing import List
from difflib import SequenceMatcher
from sourcing.models import ProductCandidate
from sourcing.config import get_config


def similarity(a: str, b: str) -> float:
    """Calculate string similarity ratio"""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def dedup_candidates(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
    """Deduplicate candidates by niche + pain_point_keywords similarity"""
    config = get_config()
    threshold = config.dedup.similarity_threshold
    
    unique = []
    for candidate in candidates:
        is_dup = False
        for existing in unique:
            # Compare niche
            niche_sim = similarity(candidate.niche, existing.niche)
            
            # Compare pain point keywords (jaccard-like)
            kw_set_1 = set(candidate.pain_point_keywords)
            kw_set_2 = set(existing.pain_point_keywords)
            if kw_set_1 and kw_set_2:
                kw_sim = len(kw_set_1 & kw_set_2) / len(kw_set_1 | kw_set_2)
            else:
                kw_sim = 0.0
            
            # Combined similarity
            combined_sim = (niche_sim + kw_sim) / 2
            
            if combined_sim >= threshold:
                # Keep the one with higher score
                if candidate.total_score > existing.total_score:
                    unique.remove(existing)
                    unique.append(candidate)
                is_dup = True
                break
        
        if not is_dup:
            unique.append(candidate)
    
    return unique