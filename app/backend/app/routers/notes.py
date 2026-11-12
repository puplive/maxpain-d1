"""研究笔记。

- 笔记：知识点 / 案例 / 经验，可关联品种和日期区间
- 验证记录：每次复查追加一条，pass/fail 自动更新笔记状态
- 间隔回顾：1/3/7/30 天，记住升一档，模糊重置
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Note, NoteCheck
from ..schemas import NoteCheckIn, NoteIn, NoteOut, NoteReviewIn

router = APIRouter(prefix="/notes", tags=["notes"])

# 回顾间隔（天）：新笔记先 1 天后回顾，连续记住逐步拉长
REVIEW_INTERVALS = (1, 3, 7, 30)


def _get_note(db: Session, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.get("", response_model=list[NoteOut])
def list_notes(db: Session = Depends(get_db)):
    """返回全部笔记（含验证记录），筛选和搜索在前端做。"""
    stmt = (
        select(Note)
        .options(selectinload(Note.checks))
        .order_by(Note.updated_at.desc())
    )
    return db.scalars(stmt).all()


@router.post("", response_model=NoteOut)
def create_note(payload: NoteIn, db: Session = Depends(get_db)):
    note = Note(
        **payload.model_dump(),
        next_review=date.today() + timedelta(days=REVIEW_INTERVALS[0]),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.put("/{note_id}", response_model=NoteOut)
def update_note(note_id: int, payload: NoteIn, db: Session = Depends(get_db)):
    note = _get_note(db, note_id)
    for key, value in payload.model_dump().items():
        setattr(note, key, value)
    db.commit()
    db.refresh(note)
    return note


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    note = _get_note(db, note_id)
    db.delete(note)
    db.commit()
    return {"ok": True}


@router.post("/{note_id}/checks", response_model=NoteOut)
def add_check(
    note_id: int, payload: NoteCheckIn, db: Session = Depends(get_db)
):
    """追加验证记录；通过/未通过会同步笔记状态，待观察不动。"""
    note = _get_note(db, note_id)
    db.add(
        NoteCheck(note_id=note.id, result=payload.result, comment=payload.comment)
    )
    if payload.result == "pass":
        note.status = "verified"
    elif payload.result == "fail":
        note.status = "invalid"
    db.commit()
    db.refresh(note)
    return note


@router.post("/{note_id}/review", response_model=NoteOut)
def review_note(
    note_id: int, payload: NoteReviewIn, db: Session = Depends(get_db)
):
    """回顾笔记：记住则进入下一档间隔，模糊则回到 1 天。"""
    note = _get_note(db, note_id)
    if payload.remembered:
        note.review_stage = min(
            note.review_stage + 1, len(REVIEW_INTERVALS) - 1
        )
    else:
        note.review_stage = 0
    note.next_review = date.today() + timedelta(
        days=REVIEW_INTERVALS[note.review_stage]
    )
    db.commit()
    db.refresh(note)
    return note
