from __future__ import annotations

import os
from typing import Optional, Dict, Any
import shopify_api
from sourcing.models import ProductCandidate
from sourcing.config import get_settings


class ShopifySync:
    """Shopify Admin API integration for creating draft products"""
    
    def __init__(self):
        settings = get_settings()
        self.shop = settings.shopify_shop
        self.token = settings.shopify_token
        self.client = None
        
        if self.shop and self.token:
            self.client = shopify_api.ShopifyAPI(self.shop, self.token)
    
    def test_connection(self) -> bool:
        """Test Shopify API connection"""
        if not self.client:
            return False
        try:
            shop = self.client.get_shop()
            return shop is not None
        except Exception:
            return False
    
    def create_draft_product(self, candidate: ProductCandidate, brand_config: Dict) -> Optional[int]:
        """Create a draft product in Shopify, return draft product ID"""
        if not self.client:
            return None
        
        try:
            draft_data = candidate.to_shopify_draft(brand_config)
            product = self.client.create_product(draft_data["product"])
            
            if product and product.get("id"):
                candidate.shopify_draft_id = product["id"]
                return product["id"]
            else:
                print(f"Shopify create failed: {product}")
                return None
        except Exception as e:
            print(f"Shopify create error: {e}")
            return None
    
    def publish_product(self, draft_id: int) -> Optional[int]:
        """Publish a draft product, return published product ID"""
        if not self.client:
            return None
        
        try:
            product = self.client.update_product(draft_id, {"published": True, "published_at": "now"})
            
            if product and product.get("id"):
                return product["id"]
            else:
                print(f"Shopify publish failed: {product}")
                return None
        except Exception as e:
            print(f"Shopify publish error: {e}")
            return None
    
    def get_product(self, product_id: int) -> Optional[Dict]:
        """Get product details"""
        if not self.client:
            return None
        
        try:
            return self.client.get_product(product_id)
        except Exception:
            return None
    
    def upload_image(self, image_url: str, product_id: int) -> bool:
        """Upload product image from URL"""
        if not self.client:
            return False
        
        try:
            return self.client.create_image(product_id, {"src": image_url})
        except Exception as e:
            print(f"Shopify image upload error: {e}")
            return False