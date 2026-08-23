"""Pydantic request models for the REST API."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class LoadRequest(BaseModel):
    filename: str = Field(..., description="File name inside data/ or an absolute path")
    sheet: Optional[str] = None


class RunRequest(BaseModel):
    date_col: str
    kpi: str
    channels: List[str]
    controls: List[str] = []
    passes: int = Field(2, ge=1, le=4, description="Coordinate-search sweeps over hyperparameters")


class OptimizeRequest(BaseModel):
    total_weekly_budget: float = Field(..., gt=0)
    min_share: float = Field(0.0, ge=0.0, le=1.0)
    max_share: float = Field(1.0, ge=0.0, le=1.0)
