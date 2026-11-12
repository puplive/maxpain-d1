"""看板筛选接口。

条件构建器所需的白名单由 services/screener.py 统一维护：
GET /screen/meta 取选项，POST /screen 传条件数组，
多个条件按 combine（and/or）组合。
"""

from fastapi import APIRouter, HTTPException

from ..schemas import ScreenMatch, ScreenMeta, ScreenRequest
from ..services.screener import screen, screen_meta, validate_conditions

router = APIRouter(prefix="/screen", tags=["screen"])


@router.get("/meta", response_model=ScreenMeta)
def get_meta() -> dict:
    """返回窗口、指标、行为、聚合方式等选项。"""
    return screen_meta()


@router.post("", response_model=list[ScreenMatch])
def run_screen(payload: ScreenRequest) -> list[dict]:
    """按原子条件筛选；symbols 为空时筛选全部品种。"""
    conditions = [cond.model_dump() for cond in payload.conditions]
    try:
        validate_conditions(conditions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return screen(conditions, payload.combine, payload.symbols)
