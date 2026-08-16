from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load .env if exists
load_dotenv()


class ScoringGates(BaseModel):
    # Gate 1: Pain Point Keywords
    gate_1_pain_point_keywords: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 2: Trend
    gate_2_trend: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 3: Margin
    gate_3_margin: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 4: Lightweight
    gate_4_lightweight: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 5: Quality
    gate_5_quality: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 6: Uniqueness
    gate_6_uniqueness: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 7: Seasonality
    gate_7_seasonality: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 8: Market Proof
    gate_8_market_proof: dict[str, Any] = Field(default_factory=dict)
    
    # Gate 9: Customer Value
    gate_9_customer_value: dict[str, Any] = Field(default_factory=dict)


class PricingModel(BaseModel):
    wholesale_to_retail_multiplier: float = 3.5
    shipping_cost_buffer_pct: float = 15.0
    platform_fee_pct: float = 2.9
    fixed_fee_usd: float = 0.30


class DedupConfig(BaseModel):
    similarity_threshold: float = 0.85


class ComplianceConfig(BaseModel):
    patent_keywords: list[str] = Field(default_factory=list)
    trademark_keywords: list[str] = Field(default_factory=list)
    cache_ttl_days: int = 30


class HealthcheckThresholds(BaseModel):
    sessions: dict[str, int] = Field(default_factory=dict)
    add_to_cart_rate_pct: dict[str, float] = Field(default_factory=dict)
    checkout_rate_pct: dict[str, float] = Field(default_factory=dict)
    cvr_pct: dict[str, float] = Field(default_factory=dict)
    return_rate_pct: dict[str, float] = Field(default_factory=dict)
    min_aov_usd: dict[str, float] = Field(default_factory=dict)
    min_ltv_orders: dict[str, float] = Field(default_factory=dict)


class HealthcheckConfig(BaseModel):
    thresholds: HealthcheckThresholds = Field(default_factory=HealthcheckThresholds)


class KeepaConfig(BaseModel):
    api_key: str = ""
    daily_token_limit: int = 100
    domain: int = 1


class WholesaleConfig(BaseModel):
    aliexpress_enabled: bool = True
    alibaba_enabled: bool = True
    max_offers_per_niche: int = 10
    request_timeout: int = 45


class APIConfig(BaseModel):
    ahrefs: dict[str, str] = Field(default_factory=dict)
    shopify: dict[str, str] = Field(default_factory=dict)
    notion: dict[str, str] = Field(default_factory=dict)
    google_patents: dict[str, str] = Field(default_factory=dict)
    uspto: dict[str, str] = Field(default_factory=dict)
    tmview: dict[str, str] = Field(default_factory=dict)
    keepa: dict[str, str] = Field(default_factory=dict)
    rainforest: dict[str, str] = Field(default_factory=dict)
    dataforseo: dict[str, str] = Field(default_factory=dict)


class BrandConfig(BaseModel):
    voice: str = ""
    usp_framework: str = ""
    trust_anchors: str = ""


class FetchConfig(BaseModel):
    max_candidates_per_niche: int = 20
    request_timeout: int = 30
    rate_limit_delay: float = 2.0


class PipelineConfig(BaseModel):
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    scoring: ScoringGates = Field(default_factory=ScoringGates)
    pricing: PricingModel = Field(default_factory=PricingModel)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)
    healthcheck: HealthcheckConfig = Field(default_factory=HealthcheckConfig)
    keepa: KeepaConfig = Field(default_factory=KeepaConfig)
    wholesale: WholesaleConfig = Field(default_factory=WholesaleConfig)
    seed_niches: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    # Notion
    notion_token: str = ""
    notion_db_id: str = ""
    
    # Shopify
    shopify_shop: str = ""
    shopify_token: str = ""
    
    # Ahrefs
    ahrefs_token: str = ""
    
    # Optional APIs
    google_patents_api_key: str = ""
    uspto_api_key: str = ""
    keepa_api_key: str = ""
    rainforest_api_key: str = ""
    dataforseo_login: str = ""
    dataforseo_password: str = ""


@lru_cache
def load_config() -> tuple[PipelineConfig, Settings]:
    """Load pipeline config from YAML and settings from .env"""
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    
    pipeline = PipelineConfig(**raw.get("pipeline", {}))
    # seed_niches 在 YAML 顶层(不在 pipeline 下), 单独合并
    if raw.get("seed_niches"):
        pipeline.seed_niches = list(raw["seed_niches"])
    settings = Settings()
    
    return pipeline, settings


def get_config() -> PipelineConfig:
    return load_config()[0]


def get_settings() -> Settings:
    return load_config()[1]