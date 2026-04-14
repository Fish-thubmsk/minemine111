"""
批处理功能单元测试
- 段落切分规则
- 映射字段生成唯一性
- 批次创建逻辑
"""
import os
import sys
import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 确保能导入 app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 设置测试环境变量（避免从 .env 加载真实配置）
os.environ.setdefault("DATABASE_URL", "sqlite:///")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:9999/v1")

from app.database import Base
from app.models.batch_models import Batch, BatchSegment, SegmentTask
from app.services.batch_service import (
    split_paragraphs,
    create_batch,
    _generate_batch_id,
    _generate_segment_id,
    _text_hash,
    _get_preview_length,
)


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def db_session():
    """创建内存数据库并返回 session"""
    engine = create_engine("sqlite:///:memory:")
    # 导入所有模型
    from app.models import models  # noqa: F401
    from app.models import batch_models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # 创建测试用户
    from app.models.models import User
    user = User(
        card_key="test-key-001",
        access_link="http://localhost/access/test",
        is_active=True,
        usage_limit=100,
        usage_count=0,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    yield session
    session.close()


@pytest.fixture
def test_user(db_session):
    from app.models.models import User
    return db_session.query(User).first()


# ─── 1. 段落切分规则 ──────────────────────────────────────

class TestSplitParagraphs:
    def test_basic_split(self):
        text = "第一段正文内容，足够长的段落。\n\n第二段正文内容，也很长的段落。"
        result = split_paragraphs(text, skip_short_threshold=0)
        assert len(result) == 2
        assert result[0] == "第一段正文内容，足够长的段落。"
        assert result[1] == "第二段正文内容，也很长的段落。"

    def test_skip_empty_lines(self):
        text = "段落一\n\n\n\n段落二\n\n段落三"
        result = split_paragraphs(text, skip_short_threshold=0)
        assert len(result) == 3

    def test_skip_blank_paragraphs(self):
        text = "段落一\n   \n段落二"
        result = split_paragraphs(text, skip_short_threshold=0)
        assert len(result) == 2

    def test_skip_short_paragraphs(self):
        text = "这是一个很长的段落内容，超过了阈值。\n短\n另一个很长的段落内容，超过了阈值。"
        result = split_paragraphs(text, skip_short_threshold=5)
        assert len(result) == 2
        # "短" 只有1字，被跳过
        assert "短" not in [r for r in result]

    def test_threshold_zero_keeps_all(self):
        text = "A\nB\nC"
        result = split_paragraphs(text, skip_short_threshold=0)
        assert len(result) == 3

    def test_empty_text(self):
        result = split_paragraphs("", skip_short_threshold=0)
        assert result == []

    def test_only_whitespace(self):
        result = split_paragraphs("   \n\n  \n  ", skip_short_threshold=0)
        assert result == []

    def test_single_newline_split(self):
        text = "段落一\n段落二"
        result = split_paragraphs(text, skip_short_threshold=0)
        assert len(result) == 2


# ─── 2. 映射字段生成唯一性 ────────────────────────────────

class TestMappingFields:
    def test_batch_id_format(self, db_session):
        batch_id = _generate_batch_id(db_session)
        assert batch_id.startswith("B")
        assert len(batch_id) == 5  # B0001

    def test_segment_id_format(self):
        seg_id = _generate_segment_id("B0001", 1)
        assert seg_id == "B0001-S0001"

    def test_segment_id_large_index(self):
        seg_id = _generate_segment_id("B0042", 999)
        assert seg_id == "B0042-S0999"

    def test_text_hash_deterministic(self):
        text = "测试文本"
        h1 = _text_hash(text)
        h2 = _text_hash(text)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_text_hash_different(self):
        assert _text_hash("A") != _text_hash("B")


# ─── 3. 批次创建逻辑 ──────────────────────────────────────

class TestCreateBatch:
    def test_basic_create(self, db_session, test_user):
        raw_text = "第一段正文内容足够长。\n\n第二段正文内容足够长。"
        batch = create_batch(
            db=db_session,
            user_id=test_user.id,
            name="测试批次",
            raw_text=raw_text,
            task_types=["polish"],
            skip_short_threshold=0,
        )
        assert batch.batch_id.startswith("B")
        assert batch.total_segments == 2
        assert batch.total_tasks == 2
        assert batch.status == "pending"

    def test_create_with_two_task_types(self, db_session, test_user):
        raw_text = "段落一内容。\n\n段落二内容。"
        batch = create_batch(
            db=db_session,
            user_id=test_user.id,
            name="双任务类型",
            raw_text=raw_text,
            task_types=["polish", "enhance"],
            skip_short_threshold=0,
        )
        assert batch.total_segments == 2
        assert batch.total_tasks == 4  # 2 segments × 2 types

    def test_create_generates_segments(self, db_session, test_user):
        raw_text = "段落A足够长内容。\n段落B足够长内容。"
        batch = create_batch(
            db=db_session,
            user_id=test_user.id,
            name="段落验证",
            raw_text=raw_text,
            task_types=["polish"],
            skip_short_threshold=0,
        )
        segments = db_session.query(BatchSegment).filter(
            BatchSegment.batch_id == batch.id
        ).order_by(BatchSegment.source_index).all()

        assert len(segments) == 2
        assert segments[0].source_index == 1
        assert segments[1].source_index == 2
        assert segments[0].segment_id == f"{batch.batch_id}-S0001"
        assert segments[1].segment_id == f"{batch.batch_id}-S0002"

    def test_create_generates_tasks(self, db_session, test_user):
        raw_text = "段落内容。"
        batch = create_batch(
            db=db_session,
            user_id=test_user.id,
            name="任务验证",
            raw_text=raw_text,
            task_types=["polish", "enhance"],
            skip_short_threshold=0,
        )
        tasks = db_session.query(SegmentTask).filter(
            SegmentTask.batch_id == batch.id
        ).all()
        assert len(tasks) == 2
        task_types = {t.task_type for t in tasks}
        assert task_types == {"polish", "enhance"}

    def test_source_preview(self, db_session, test_user):
        long_text = "这是一段非常长的正文内容" * 10
        raw_text = long_text
        batch = create_batch(
            db=db_session,
            user_id=test_user.id,
            name="预览验证",
            raw_text=raw_text,
            task_types=["polish"],
            skip_short_threshold=0,
        )
        seg = db_session.query(BatchSegment).filter(
            BatchSegment.batch_id == batch.id
        ).first()
        assert len(seg.source_preview) <= _get_preview_length()

    def test_invalid_task_type_raises(self, db_session, test_user):
        with pytest.raises(ValueError, match="不支持的任务类型"):
            create_batch(
                db=db_session,
                user_id=test_user.id,
                name="invalid",
                raw_text="段落内容。",
                task_types=["invalid"],
                skip_short_threshold=0,
            )

    def test_empty_text_raises(self, db_session, test_user):
        with pytest.raises(ValueError, match="无有效段落"):
            create_batch(
                db=db_session,
                user_id=test_user.id,
                name="empty",
                raw_text="   ",
                task_types=["polish"],
                skip_short_threshold=0,
            )

    def test_source_text_hash(self, db_session, test_user):
        raw_text = "特定段落内容。"
        batch = create_batch(
            db=db_session,
            user_id=test_user.id,
            name="hash验证",
            raw_text=raw_text,
            task_types=["polish"],
            skip_short_threshold=0,
        )
        seg = db_session.query(BatchSegment).filter(
            BatchSegment.batch_id == batch.id
        ).first()
        expected = hashlib.sha256("特定段落内容。".encode("utf-8")).hexdigest()
        assert seg.source_text_hash == expected
