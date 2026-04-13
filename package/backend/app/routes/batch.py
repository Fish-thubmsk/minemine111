"""
批处理 API 路由
"""
import json
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.batch_models import Batch, BatchSegment, SegmentTask
from app.models.models import User
from app.schemas.batch_schemas import (
    BatchCreate,
    BatchCreateResponse,
    BatchDetailResponse,
    BatchExportItem,
    BatchSummaryResponse,
    SegmentResponse,
    TaskListResponse,
    TaskResponse,
)
from app.services.batch_service import create_batch, execute_batch
from datetime import datetime

router = APIRouter(prefix="/batches", tags=["batches"])


# ── helpers ──────────────────────────────────────────────

def _get_user(card_key: str, db: Session) -> User:
    user = (
        db.query(User)
        .filter(User.card_key == card_key, User.is_active.is_(True))
        .first()
    )
    if not user:
        raise HTTPException(status_code=401, detail="无效的卡密")
    user.last_used = datetime.utcnow()
    db.commit()
    return user


def _get_batch(batch_id: str, user_id: int, db: Session) -> Batch:
    batch = (
        db.query(Batch)
        .filter(Batch.batch_id == batch_id, Batch.user_id == user_id)
        .first()
    )
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return batch


def _batch_progress(batch: Batch) -> float:
    if batch.total_tasks == 0:
        return 0.0
    return round((batch.completed_tasks / batch.total_tasks) * 100, 1)


def _to_summary(batch: Batch) -> dict:
    return {
        "id": batch.id,
        "batch_id": batch.batch_id,
        "name": batch.name,
        "status": batch.status,
        "total_segments": batch.total_segments,
        "total_tasks": batch.total_tasks,
        "completed_tasks": batch.completed_tasks,
        "failed_tasks": batch.failed_tasks,
        "progress": _batch_progress(batch),
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
    }


# ── 1. POST /api/batches ─────────────────────────────────

@router.post("", response_model=BatchCreateResponse)
async def api_create_batch(
    data: BatchCreate,
    card_key: str = Query(...),
    db: Session = Depends(get_db),
):
    """创建批次（切分段落 + 生成任务）"""
    user = _get_user(card_key, db)
    try:
        batch = create_batch(
            db=db,
            user_id=user.id,
            name=data.name,
            raw_text=data.raw_text,
            task_types=data.task_types,
            skip_short_threshold=data.skip_short_threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return BatchCreateResponse(
        batch_id=batch.batch_id,
        name=batch.name,
        total_segments=batch.total_segments,
        total_tasks=batch.total_tasks,
        status=batch.status,
    )


# ── 2. POST /api/batches/{batch_id}/start ────────────────

async def _run_batch_background(batch_id: int, db: Session):
    """后台执行批次"""
    await execute_batch(db, batch_id)


@router.post("/{batch_id}/start")
async def api_start_batch(
    batch_id: str,
    background_tasks: BackgroundTasks,
    card_key: str = Query(...),
    db: Session = Depends(get_db),
):
    """启动批次执行"""
    user = _get_user(card_key, db)
    batch = _get_batch(batch_id, user.id, db)

    if batch.status not in ("pending", "partial_failed", "failed"):
        raise HTTPException(status_code=400, detail="当前状态不允许启动")

    batch.status = "running"
    db.commit()

    background_tasks.add_task(_run_batch_background, batch.id, db)
    return {"message": "批次已启动", "batch_id": batch.batch_id}


# ── 3. GET /api/batches/{batch_id} ───────────────────────

@router.get("/{batch_id}", response_model=BatchDetailResponse)
async def api_get_batch(
    batch_id: str,
    card_key: str = Query(...),
    db: Session = Depends(get_db),
):
    """获取批次概要与段落"""
    user = _get_user(card_key, db)
    batch = _get_batch(batch_id, user.id, db)

    segments = (
        db.query(BatchSegment)
        .filter(BatchSegment.batch_id == batch.id)
        .order_by(BatchSegment.source_index)
        .all()
    )

    return BatchDetailResponse(
        **_to_summary(batch),
        segments=[
            SegmentResponse(
                id=s.id,
                segment_id=s.segment_id,
                source_index=s.source_index,
                source_text=s.source_text,
                source_preview=s.source_preview,
                source_text_hash=s.source_text_hash,
            )
            for s in segments
        ],
    )


# ── 4. GET /api/batches/{batch_id}/tasks ─────────────────

@router.get("/{batch_id}/tasks", response_model=TaskListResponse)
async def api_list_tasks(
    batch_id: str,
    card_key: str = Query(...),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """列出批次下的任务（分页、筛选、按 source_index 排序）"""
    user = _get_user(card_key, db)
    batch = _get_batch(batch_id, user.id, db)

    query = (
        db.query(SegmentTask, BatchSegment)
        .join(BatchSegment, SegmentTask.segment_id == BatchSegment.id)
        .filter(SegmentTask.batch_id == batch.id)
    )
    if status:
        query = query.filter(SegmentTask.status == status)

    total = query.count()
    rows = (
        query.order_by(BatchSegment.source_index, SegmentTask.task_type)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    tasks = []
    for task, seg in rows:
        tasks.append(
            TaskResponse(
                id=task.id,
                batch_id=task.batch_id,
                segment_id=task.segment_id,
                segment_display_id=seg.segment_id,
                source_index=seg.source_index,
                source_preview=seg.source_preview,
                task_type=task.task_type,
                status=task.status,
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                result_text=task.result_text,
                error_code=task.error_code,
                error_message=task.error_message,
                started_at=task.started_at,
                finished_at=task.finished_at,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    return TaskListResponse(tasks=tasks, total=total, page=page, page_size=page_size)


# ── 5. POST /api/batches/tasks/{task_id}/retry ───────────

@router.post("/tasks/{task_id}/retry")
async def api_retry_task(
    task_id: int,
    background_tasks: BackgroundTasks,
    card_key: str = Query(...),
    db: Session = Depends(get_db),
):
    """单任务重试"""
    user = _get_user(card_key, db)
    task = db.query(SegmentTask).filter(SegmentTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    batch = _get_batch(
        db.query(Batch).filter(Batch.id == task.batch_id).first().batch_id,
        user.id,
        db,
    )

    if task.status != "failed":
        raise HTTPException(status_code=400, detail="仅可重试失败的任务")

    task.status = "pending"
    task.retry_count = 0
    task.error_code = None
    task.error_message = None
    db.commit()

    background_tasks.add_task(_run_batch_background, batch.id, db)
    return {"message": "任务已重新排队"}


# ── 6. POST /api/batches/{batch_id}/retry-failed ─────────

@router.post("/{batch_id}/retry-failed")
async def api_retry_failed(
    batch_id: str,
    background_tasks: BackgroundTasks,
    card_key: str = Query(...),
    db: Session = Depends(get_db),
):
    """批量重试失败任务"""
    user = _get_user(card_key, db)
    batch = _get_batch(batch_id, user.id, db)

    failed_tasks = (
        db.query(SegmentTask)
        .filter(SegmentTask.batch_id == batch.id, SegmentTask.status == "failed")
        .all()
    )
    if not failed_tasks:
        raise HTTPException(status_code=400, detail="没有失败的任务")

    for t in failed_tasks:
        t.status = "pending"
        t.retry_count = 0
        t.error_code = None
        t.error_message = None
    db.commit()

    background_tasks.add_task(_run_batch_background, batch.id, db)
    return {"message": f"已重新排队 {len(failed_tasks)} 个失败任务"}


# ── 7. GET /api/batches/{batch_id}/export ─────────────────

@router.get("/{batch_id}/export")
async def api_export_batch(
    batch_id: str,
    card_key: str = Query(...),
    fmt: str = Query("json", regex="^(json|csv)$"),
    db: Session = Depends(get_db),
):
    """导出映射结果 (JSON / CSV)"""
    user = _get_user(card_key, db)
    batch = _get_batch(batch_id, user.id, db)

    rows = (
        db.query(SegmentTask, BatchSegment)
        .join(BatchSegment, SegmentTask.segment_id == BatchSegment.id)
        .filter(SegmentTask.batch_id == batch.id)
        .order_by(BatchSegment.source_index, SegmentTask.task_type)
        .all()
    )

    items = []
    for task, seg in rows:
        items.append(
            BatchExportItem(
                source_index=seg.source_index,
                segment_id=seg.segment_id,
                source_preview=seg.source_preview,
                source_text=seg.source_text,
                task_type=task.task_type,
                status=task.status,
                result_text=task.result_text,
            )
        )

    if fmt == "csv":
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["source_index", "segment_id", "source_preview", "source_text", "task_type", "status", "result_text"]
        )
        for item in items:
            writer.writerow(
                [item.source_index, item.segment_id, item.source_preview, item.source_text, item.task_type, item.status, item.result_text or ""]
            )
        return JSONResponse(
            content={"format": "csv", "content": buf.getvalue(), "filename": f"batch_{batch_id}.csv"},
        )

    return JSONResponse(
        content={
            "format": "json",
            "content": [item.model_dump() for item in items],
            "filename": f"batch_{batch_id}.json",
        },
    )


# ── 8. GET /api/batches (list) ────────────────────────────

@router.get("", response_model=List[BatchSummaryResponse])
async def api_list_batches(
    card_key: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """列出当前用户所有批次"""
    user = _get_user(card_key, db)
    batches = (
        db.query(Batch)
        .filter(Batch.user_id == user.id)
        .order_by(Batch.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [BatchSummaryResponse(**_to_summary(b)) for b in batches]
