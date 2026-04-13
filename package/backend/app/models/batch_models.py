from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey, Float, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Batch(Base):
    """批处理批次表"""
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(String(50), unique=True, index=True, nullable=False)  # e.g. B0001
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(255), nullable=False)
    status = Column(String(50), default="pending", index=True)
    # status: pending | running | completed | failed | partial_failed | cancelled
    total_segments = Column(Integer, default=0)
    total_tasks = Column(Integer, default=0)
    completed_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    segments = relationship("BatchSegment", back_populates="batch", cascade="all, delete-orphan")
    tasks = relationship("SegmentTask", back_populates="batch", cascade="all, delete-orphan")


class BatchSegment(Base):
    """批处理段落表"""
    __tablename__ = "batch_segments"

    id = Column(Integer, primary_key=True, index=True)
    segment_id = Column(String(50), unique=True, index=True, nullable=False)  # e.g. B0001-S0001
    batch_id = Column(Integer, ForeignKey("batches.id"), index=True, nullable=False)
    source_index = Column(Integer, nullable=False)  # 1-based
    source_text = Column(Text, nullable=False)
    source_preview = Column(String(200), nullable=False)
    source_text_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    batch = relationship("Batch", back_populates="segments")
    tasks = relationship("SegmentTask", back_populates="segment", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_batch_segment_batch_source", "batch_id", "source_index", unique=True),
    )


class SegmentTask(Base):
    """段落任务表"""
    __tablename__ = "segment_tasks"

    id = Column(Integer, primary_key=True, index=True)
    batch_id = Column(Integer, ForeignKey("batches.id"), index=True, nullable=False)
    segment_id = Column(Integer, ForeignKey("batch_segments.id"), index=True, nullable=False)
    task_type = Column(String(50), nullable=False)  # polish | enhance
    status = Column(String(50), default="pending", index=True)
    # status: pending | running | done | failed | retrying
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=2)
    result_text = Column(Text, nullable=True)
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    batch = relationship("Batch", back_populates="tasks")
    segment = relationship("BatchSegment", back_populates="tasks")

    __table_args__ = (
        Index("ix_segment_task_batch_status", "batch_id", "status"),
        Index("ix_segment_task_segment_type", "segment_id", "task_type"),
    )
