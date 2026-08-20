#!/usr/bin/env python3
"""apps/control_api/schemas/ — Pydantic request/response validation schemas."""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class SourceStatus(str, Enum):
    discovered = "discovered"
    downloaded = "downloaded"
    extracted = "extracted"
    indexed = "indexed"
    quarantine = "quarantine"
    error = "error"


class RegisterSourceRequest(BaseModel):
    drive_item_id: str = Field(..., description="OneDrive driveItem ID")
    drive_id: str = Field(..., description="OneDrive drive ID")
    file_path: str = Field(..., description="Full file path from root")
    file_name: str = Field(..., description="File name")
    mime_type: str = Field(..., description="MIME type")
    size_bytes: int = Field(..., ge=0)
    sha256_hash: str = Field(..., length=64)
    status: SourceStatus = SourceStatus.discovered


class UnitUpsertRequest(BaseModel):
    source_id: str
    doc_id: str
    unit_index: int
    parent_unit_id: Optional[str] = None
    heading_path: list[str] = Field(default_factory=list)
    unit_type: str
    raw_text: str
    clean_text: str
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    page_number: Optional[int] = None
    bbox_coords: Optional[dict] = None
    content_hash: str = Field(..., length=64)
    disposition: str = "authoritative"
    quality_score: float = 1.0


class PublishPageRequest(BaseModel):
    file_path: str
    title: str
    page_type: str
    domain: str
    frontmatter: dict = Field(default_factory=dict)
    markdown_body: str
    source_unit_ids: list[str] = Field(default_factory=list)


class EmbeddingUpsertRequest(BaseModel):
    content_hash: str = Field(..., length=64)
    model_id: str = "BAAI/bge-m3"
    dense_vector: list[float]
    sparse_weights: Optional[dict] = None


class HealthResponse(BaseModel):
    status: str
    postgres: bool
    redis: bool
    disk_space_gb: float