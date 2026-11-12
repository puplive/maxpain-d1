"""期权到期日历。

数据来自 build_web_data.py 生成的 web/data/expiries.json：
每个品种按「当天最近到期日」去重，记录历史至今出现过的到期日。
近月合约逐日滚动，所以每个品种的最新一条就是下一个到期日。
"""

import json

from fastapi import APIRouter

from ..config import settings
from ..schemas import ExpiryEvent

router = APIRouter(prefix="/expiries", tags=["expiries"])


@router.get("", response_model=list[ExpiryEvent])
def list_expiries() -> list[dict]:
    """返回全部品种的到期记录；文件缺失时返回空列表。"""
    path = settings.web_data_dir / "expiries.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("items", [])
