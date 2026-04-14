from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class BatchCreate(BaseModel):
    """创建批次请求"""
    name: str = Field(..., min_length=1, max_length=255, description="批次名称")
    raw_text: str = Field(..., min_length=1, description="原始正文文本")
    task_types: List[str] = Field(default=["polish"], description="任务类型列表: polish, enhance")
    skip_short_threshold: Optional[int] = Field(default=None, description="跳过短段落阈值")


class BatchCreateResponse(BaseModel):
    """创建批次响应"""
    batch_id: str
    name: str
    total_segments: int
    total_tasks: int
    status: str

    class Config:
        from_attributes = True


class BatchSummaryResponse(BaseModel):
    """批次概要响应"""
    id: int
    batch_id: str
    name: str
    status: str
    total_segments: int
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    progress: float = 0.0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SegmentResponse(BaseModel):
    """段落响应"""
    id: int
    segment_id: str
    source_index: int
    source_text: str
    source_preview: str
    source_text_hash: Optional[str] = None

    class Config:
        from_attributes = True


class TaskResponse(BaseModel):
    """任务响应"""
    id: int
    batch_id: int
    segment_id: int
    segment_display_id: str = ""
    source_index: int = 0
    source_preview: str = ""
    task_type: str
    status: str
    retry_count: int
    max_retries: int
    result_text: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """任务列表响应"""
    tasks: List[TaskResponse]
    total: int
    page: int
    page_size: int


class BatchDetailResponse(BatchSummaryResponse):
    """批次详情响应（含段落）"""
    segments: List[SegmentResponse] = []


class BatchExportItem(BaseModel):
    """导出项"""
    source_index: int
    segment_id: str
    source_preview: str
    source_text: str
    task_type: str
    status: str
    result_text: Optional[str] = None
