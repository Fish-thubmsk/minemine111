"""
批处理服务 - 段落切分、任务创建、并发执行、失败重试
"""
import asyncio
import hashlib
import re
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.batch_models import Batch, BatchSegment, SegmentTask
from app.services.ai_service import (
    AIService,
    get_default_polish_prompt,
    get_default_enhance_prompt,
    is_retryable_error,
    get_error_category,
)


def _get_max_retries():
    return settings.BATCH_MAX_RETRIES


def _get_retry_interval():
    return settings.BATCH_RETRY_INTERVAL_SECONDS


def _get_preview_length():
    return settings.SOURCE_PREVIEW_LENGTH


def split_paragraphs(
    raw_text: str,
    skip_short_threshold: Optional[int] = None,
) -> List[str]:
    """将正文按段落切分

    切分规则:
    - 按空行 / 换行符切分
    - 跳过空白段
    - 可选跳过短段 (沿用 SEGMENT_SKIP_THRESHOLD)

    Returns:
        有序段落列表
    """
    threshold = skip_short_threshold if skip_short_threshold is not None else settings.SEGMENT_SKIP_THRESHOLD

    paragraphs: List[str] = []
    for line in re.split(r'\n\s*\n|\n', raw_text):
        text = line.strip()
        if not text:
            continue
        if threshold > 0 and len(text) < threshold:
            continue
        paragraphs.append(text)

    return paragraphs


def _generate_batch_id(db: Session) -> str:
    """生成递增的批次 ID, 如 B0001"""
    last = db.query(Batch).order_by(Batch.id.desc()).first()
    next_num = (last.id + 1) if last else 1
    return f"B{next_num:04d}"


def _generate_segment_id(batch_id_str: str, source_index: int) -> str:
    """生成段落 ID, 如 B0001-S0012"""
    return f"{batch_id_str}-S{source_index:04d}"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def create_batch(
    db: Session,
    user_id: int,
    name: str,
    raw_text: str,
    task_types: List[str],
    skip_short_threshold: Optional[int] = None,
) -> Batch:
    """创建批次, 切分段落并生成任务"""

    # 验证 task_types
    valid_types = {"polish", "enhance"}
    for t in task_types:
        if t not in valid_types:
            raise ValueError(f"不支持的任务类型: {t}, 仅支持 {valid_types}")

    # 切分段落
    paragraphs = split_paragraphs(raw_text, skip_short_threshold)
    if not paragraphs:
        raise ValueError("切分后无有效段落，请检查输入文本")

    # 创建批次
    batch_id_str = _generate_batch_id(db)
    batch = Batch(
        batch_id=batch_id_str,
        user_id=user_id,
        name=name,
        status="pending",
        total_segments=len(paragraphs),
        total_tasks=len(paragraphs) * len(task_types),
    )
    db.add(batch)
    db.flush()  # 获取 batch.id

    # 创建段落和任务
    for idx, para_text in enumerate(paragraphs, start=1):
        seg_id_str = _generate_segment_id(batch_id_str, idx)
        preview = para_text[:_get_preview_length()]
        seg = BatchSegment(
            segment_id=seg_id_str,
            batch_id=batch.id,
            source_index=idx,
            source_text=para_text,
            source_preview=preview,
            source_text_hash=_text_hash(para_text),
        )
        db.add(seg)
        db.flush()  # 获取 seg.id

        for task_type in task_types:
            task = SegmentTask(
                batch_id=batch.id,
                segment_id=seg.id,
                task_type=task_type,
                status="pending",
                max_retries=_get_max_retries(),
            )
            db.add(task)

    db.commit()
    db.refresh(batch)
    return batch


def _update_batch_counters(db: Session, batch: Batch):
    """根据任务状态刷新批次的计数器和整体状态"""
    tasks = db.query(SegmentTask).filter(SegmentTask.batch_id == batch.id).all()

    done = sum(1 for t in tasks if t.status == "done")
    failed = sum(1 for t in tasks if t.status == "failed")
    total = len(tasks)

    batch.completed_tasks = done
    batch.failed_tasks = failed
    batch.total_tasks = total

    if done == total:
        batch.status = "completed"
    elif failed > 0 and (done + failed) == total:
        batch.status = "partial_failed"
    elif done + failed < total:
        batch.status = "running"

    db.commit()


async def _run_single_task(
    db: Session,
    task: SegmentTask,
    segment: BatchSegment,
    ai_service: AIService,
    prompt: str,
):
    """执行单个任务 (含重试)"""
    task.status = "running"
    task.started_at = datetime.utcnow()
    db.commit()

    last_error: Optional[Exception] = None

    attempts = 1 + task.max_retries  # 首次 + 重试次数
    for attempt in range(attempts):
        try:
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": segment.source_text},
            ]

            # 使用非流式调用
            result = await ai_service.complete(messages, temperature=0.7)

            task.result_text = result
            task.status = "done"
            task.finished_at = datetime.utcnow()
            task.error_code = None
            task.error_message = None
            db.commit()
            return  # 成功

        except Exception as exc:
            last_error = exc
            task.retry_count = attempt + 1

            if attempt < task.max_retries:
                # 判断是否可重试
                if not is_retryable_error(exc):
                    break  # 不可重试, 直接失败
                task.status = "retrying"
                task.error_message = f"[重试 {attempt + 1}] {str(exc)[:300]}"
                db.commit()
                await asyncio.sleep(_get_retry_interval() * (attempt + 1))
            # 继续下一次尝试

    # 所有重试用尽, 标记失败
    task.status = "failed"
    task.finished_at = datetime.utcnow()
    if last_error:
        task.error_code = get_error_category(last_error)
        task.error_message = str(last_error)[:500]
    db.commit()


async def execute_batch(db: Session, batch_id: int):
    """执行整个批次的所有待处理任务 (并发)"""

    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        return

    batch.status = "running"
    db.commit()

    # 初始化 AI 服务
    polish_service: Optional[AIService] = None
    enhance_service: Optional[AIService] = None

    try:
        polish_service = AIService(
            model=settings.POLISH_MODEL,
            api_key=settings.POLISH_API_KEY,
            base_url=settings.POLISH_BASE_URL,
        )
    except Exception:
        pass

    try:
        enhance_service = AIService(
            model=settings.ENHANCE_MODEL,
            api_key=settings.ENHANCE_API_KEY,
            base_url=settings.ENHANCE_BASE_URL,
        )
    except Exception:
        pass

    polish_prompt = get_default_polish_prompt()
    enhance_prompt = get_default_enhance_prompt()

    # 获取所有待处理 / 重试中的任务
    pending_tasks = (
        db.query(SegmentTask)
        .filter(
            SegmentTask.batch_id == batch.id,
            SegmentTask.status.in_(["pending", "retrying", "failed"]),
        )
        .all()
    )

    max_concurrency = settings.BATCH_MAX_CONCURRENCY
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _limited_run(task: SegmentTask):
        async with semaphore:
            seg = db.query(BatchSegment).filter(BatchSegment.id == task.segment_id).first()
            if not seg:
                return
            if task.task_type == "polish" and polish_service:
                await _run_single_task(db, task, seg, polish_service, polish_prompt)
            elif task.task_type == "enhance" and enhance_service:
                await _run_single_task(db, task, seg, enhance_service, enhance_prompt)
            else:
                task.status = "failed"
                task.error_message = f"AI 服务 ({task.task_type}) 未配置"
                task.finished_at = datetime.utcnow()
                db.commit()

    # 重置将要重试的失败任务
    for t in pending_tasks:
        if t.status == "failed":
            t.status = "pending"
            t.error_code = None
            t.error_message = None
            db.commit()

    # 并发执行
    await asyncio.gather(*[_limited_run(t) for t in pending_tasks], return_exceptions=True)

    # 更新批次计数器和状态
    _update_batch_counters(db, batch)
