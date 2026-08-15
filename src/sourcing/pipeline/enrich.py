from __future__ import annotations

from typing import List
from sourcing.models import ProductCandidate
from sourcing.config import get_config


def enrich_candidate(candidate: ProductCandidate) -> ProductCandidate:
    """Enrich candidate with calculated fields: retail price, shipping cost, margin"""
    config = get_config()
    pricing = config.pricing
    
    # If wholesale_price_usd is 0, try to use a default or skip enrichment
    if candidate.wholesale_price_usd <= 0:
        # Set defaults for missing wholesale price
        candidate.estimated_retail_price_usd = 0.0
        candidate.estimated_shipping_usd = 0.0
        candidate.estimated_margin_pct = 0.0
        if not candidate.shipping_channel:
            if candidate.weight_g <= 200:
                candidate.shipping_channel = "ePacket"
            else:
                candidate.shipping_channel = "4PX"
        return candidate
    
    # Estimated retail price = wholesale * multiplier
    candidate.estimated_retail_price_usd = round(
        candidate.wholesale_price_usd * pricing.wholesale_to_retail_multiplier, 2
    )
    
    # Estimated shipping cost (simplified: based on weight + buffer)
    # Base shipping: $3 for <100g, $5 for 100-200g, $8 for 200-500g
    if candidate.weight_g <= 100:
        base_shipping = 3.0
    elif candidate.weight_g <= 200:
        base_shipping = 5.0
    else:
        base_shipping = 8.0
    
    candidate.estimated_shipping_usd = round(
        base_shipping * (1 + pricing.shipping_cost_buffer_pct / 100), 2
    )
    
    # Estimated margin = (retail - wholesale - shipping - fees) / retail
    platform_fee = candidate.estimated_retail_price_usd * pricing.platform_fee_pct / 100 + pricing.fixed_fee_usd
    total_cost = candidate.wholesale_price_usd + candidate.estimated_shipping_usd + platform_fee
    candidate.estimated_margin_pct = round(
        (candidate.estimated_retail_price_usd - total_cost) / candidate.estimated_retail_price_usd * 100, 1
    )
    
    # Set default shipping channel if not set
    if not candidate.shipping_channel:
        if candidate.weight_g <= 200:
            candidate.shipping_channel = "ePacket"
        else:
            candidate.shipping_channel = "4PX"
    
    return candidate


def enrich_candidates(candidates: List[ProductCandidate]) -> List[ProductCandidate]:
    """Enrich a list of candidates"""
    return [enrich_candidate(c) for c in candidates]