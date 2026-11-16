"""管理、MCP 和不可变媒体清单的边界模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MediaConfig(BaseModel):
    """复用已启用模型配置；未配置能力在执行时明确拒绝。"""

    model_config = ConfigDict(extra="forbid")
    embedding_model: str | None = None
    rerank_model: str | None = None
    understanding_model: str | None = None


class DocumentImageAutoIndexSettings(BaseModel):
    """控制解析后是否为正文引用图片建立独立视觉索引。"""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    embedding_model: str | None = None

    @model_validator(mode="after")
    def require_embedding_model_when_enabled(self):
        """启用自动索引时必须绑定明确的向量空间。"""
        if self.enabled and not self.embedding_model:
            raise ValueError("启用文档图片自动索引时必须选择图像向量模型")
        return self


class MediaAsset(BaseModel):
    """一个实际对象及其来源时间或原文位置。"""

    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=100)
    kind: Literal["image", "audio", "video", "frame"]
    object: dict[str, str]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=100 * 1024 * 1024)
    mime_type: Literal["image/png", "image/jpeg", "image/webp", "audio/wav", "video/mp4"]
    segment_id: str = Field(min_length=1, max_length=100)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, gt=0)
    line: int | None = Field(default=None, ge=1)
    document_asset_id: str | None = None
    document_asset_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_range(self):
        """时间码必须同时存在且严格递增。"""
        if (self.start_ms is None) != (self.end_ms is None) or (self.start_ms is not None and self.end_ms <= self.start_ms):
            raise ValueError("媒体时间码范围无效")
        if self.kind in {"audio", "video", "frame"} and self.start_ms is None:
            raise ValueError("音视频资产需要原件时间码")
        return self


class MediaManifest(BaseModel):
    """Producer 发布的完整媒体清单，不包含模型猜测的正文。"""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    kb_id: str
    file_id: str
    source_revision: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    basis_document_revision: str | None = None
    basis_document_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    modality: Literal["image", "audio", "video", "document_images"]
    source_mime_type: str = Field(min_length=1, max_length=100)
    duration_ms: int | None = Field(default=None, gt=0, le=600_000)
    config: MediaConfig
    embedding_dimension: int | None = Field(default=None, gt=0, le=8192)
    embedding_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    assets: list[MediaAsset] = Field(min_length=1, max_length=500)
    frame_interval_ms: int | None = Field(default=None, gt=0)
    coverage: Literal["complete", "sampled_frames"]

    @model_validator(mode="after")
    def validate_assets(self):
        """防止重复资产、越界时间和漏段被声明为完整。"""
        if len({asset.asset_id for asset in self.assets}) != len(self.assets):
            raise ValueError("媒体资产 ID 重复")
        if self.modality == "document_images" and (not self.basis_document_revision or not self.basis_document_sha256):
            raise ValueError("文档图片索引必须绑定实际正文")
        if any(asset.kind in {"image", "frame"} for asset in self.assets) and (
            not self.embedding_fingerprint or self.embedding_dimension is None
        ):
            raise ValueError("视觉资产必须绑定向量空间")
        if self.modality in {"audio", "video"}:
            if self.duration_ms is None:
                raise ValueError("音视频清单缺少总时长")
            segments = sorted((a.start_ms, a.end_ms) for a in self.assets if a.kind == self.modality)
            if not segments or segments[0][0] != 0 or segments[-1][1] != self.duration_ms:
                raise ValueError("媒体片段没有覆盖完整原件")
            if any(a[1] != b[0] for a, b in zip(segments, segments[1:])):
                raise ValueError("媒体片段有空缺或重叠")
            if self.modality == "video":
                audio = sorted((asset.start_ms, asset.end_ms) for asset in self.assets if asset.kind == "audio")
                if audio and audio != segments:
                    raise ValueError("视频音轨与画面片段时间码不一致")
        for asset in self.assets:
            if asset.end_ms is not None and (self.duration_ms is None or asset.end_ms > self.duration_ms):
                raise ValueError("媒体时间码超过原件")
        return self


class IndexMediaInput(BaseModel):
    """管理端提交一个已归属文件的媒体构建。"""

    model_config = ConfigDict(extra="forbid")
    config: MediaConfig
    document_images: bool = False


class SearchMediaInput(BaseModel):
    """媒体搜索只允许收窄文件范围，并报告分页候选覆盖。"""

    model_config = ConfigDict(extra="forbid")
    kb_id: str
    query: str = Field(min_length=1, max_length=2000)
    modality: Literal["image", "audio", "video"]
    file_ids: list[str] | None = Field(default=None, max_length=100)
    limit: int = Field(default=5, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=10000)
    candidate_limit: int = Field(default=12, ge=1, le=30)


class ListMediaInput(BaseModel):
    """分页发现已授权的当前媒体及片段，不表示已经读取原件。"""

    model_config = ConfigDict(extra="forbid")
    kb_id: str
    file_ids: list[str] | None = Field(default=None, max_length=100)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=100)


class ReadMediaInput(BaseModel):
    """读取指定当前资产，原始文件使用 asset_id=original。"""

    model_config = ConfigDict(extra="forbid")
    kb_id: str
    file_id: str
    asset_id: str = Field(min_length=1, max_length=100)
    expected_revision: str


MAX_INSPECTION_TASK_CHARACTERS = 65_536


class InspectMediaInput(ReadMediaInput):
    """原件检视任务可包含完整候选与来源归属，不沿用短检索query的长度上限。"""

    question: str = Field(min_length=1, max_length=MAX_INSPECTION_TASK_CHARACTERS)
