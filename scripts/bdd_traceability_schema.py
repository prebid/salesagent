"""Pydantic schema for bdd-traceability.yaml validation.

Used by compile_bdd.py and the structural guard to validate
the traceability mapping file.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class MappingStatus(str, Enum):
    new = "new"
    mapped = "mapped"
    stale = "stale"
    conflict = "conflict"


class ScenarioMapping(BaseModel):
    adcp_scenario_id: str
    adcp_feature: str
    obligation_id: str | None = None
    upstream_refs: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    status: MappingStatus


class SourceInfo(BaseModel):
    repository: str = "adcp-req"
    commit: str | None = None
    compiled_at: str | None = None


class BDDTraceabilityMapping(BaseModel):
    schema_version: int = 1
    source: SourceInfo = Field(default_factory=SourceInfo)
    mappings: dict[str, list[ScenarioMapping]] = Field(default_factory=dict)
