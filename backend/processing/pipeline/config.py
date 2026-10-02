import re
from typing import Literal, cast

from pydantic import Field, model_validator

from backend.schemas.pipeline import Contract, Modality


class PipelineConfig(Contract):
    parser_priority: list[str] = Field(default_factory=lambda: ["native", "layout", "ocr"])
    adapter_factories: list[str] = Field(default_factory=list)
    adapter_revision: str = "1"
    supported_modalities: list[Modality] = Field(
        default_factory=lambda: cast(
            list[Modality],
            [
                "pdf",
                "docx",
                "pptx",
                "html",
                "markdown",
                "text",
                "csv",
                "xlsx",
                "json",
                "xml",
                "yaml",
                "code",
                "image",
                "audio",
                "video",
                "email",
                "log",
                "transcript",
            ],
        )
    )
    min_page_coverage: float = Field(default=1, gt=0, le=1)
    min_text_coverage: float = Field(default=0.95, gt=0, le=1)
    max_garbled_ratio: float = Field(default=0.01, ge=0, le=1)
    min_ocr_confidence: float = Field(default=0.65, ge=0, le=1)
    min_reading_order_confidence: float = Field(default=0.8, ge=0, le=1)
    max_duplicate_rate: float = Field(default=0.5, ge=0, le=1)
    max_chunk_tokens: int = Field(default=1024, ge=64, le=32000)
    min_chunk_tokens: int = Field(default=16, ge=1)
    tokenizer: Literal["utf8_bytes", "tiktoken"] = "utf8_bytes"
    tokenizer_encoding: str | None = None
    semantic_refinement: bool = True
    semantic_breakpoint: float = Field(default=0.15, ge=0, le=1)
    table_rows_per_chunk: int = Field(default=50, ge=1, le=10000)
    merge_small_chunks: bool = True
    ocr_enabled: bool = False
    ocr_language: str = Field(default="eng", pattern=r"^[a-zA-Z0-9_+]+$")
    parser_timeout_seconds: float = Field(default=60, gt=0, le=600)
    processing_timeout_seconds: float = Field(default=180, gt=0, le=1800)
    process_memory_mb: int = Field(default=1024, ge=256, le=8192)
    max_result_bytes: int = Field(default=128 * 1024 * 1024, ge=1024, le=512 * 1024 * 1024)
    max_pages: int = Field(default=1000, ge=1)
    max_blocks: int = Field(default=20000, ge=1)
    max_characters: int = Field(default=5_000_000, ge=1)
    max_chunks: int = Field(default=10000, ge=1)
    cache_enabled: bool = True

    @model_validator(mode="after")
    def consistent(self) -> "PipelineConfig":
        if self.min_chunk_tokens >= self.max_chunk_tokens:
            raise ValueError("Minimum tokens must be smaller than maximum tokens")
        if self.tokenizer == "tiktoken" and not self.tokenizer_encoding:
            raise ValueError("Configure an explicit tokenizer encoding")
        if not self.parser_priority or len(self.parser_priority) != len(set(self.parser_priority)):
            raise ValueError("Parser priorities must be nonempty and unique")
        if any(not re.fullmatch(r"[a-zA-Z_][\w.]*:[a-zA-Z_]\w*", value) for value in self.adapter_factories):
            raise ValueError("Adapter factories must be installed module:factory references")
        return self
