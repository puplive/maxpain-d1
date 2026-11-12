"""FastAPI 入口。

启动时自动建表；所有业务路由挂在 /api 前缀下，方便前端走同一个代理。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import models  # noqa: F401  导入模型，让 Base 知道有哪些表
from .config import settings
from .db import Base, engine
from .routers import (
    backtest,
    expiries,
    notes,
    optimize,
    params,
    scan,
    screen,
    signals,
    symbols,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 本地开发用 create_all 建表；表结构稳定后再引入 Alembic 做迁移
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="MaxPain Workbench API", lifespan=lifespan)

# 开发时前端是 5173 端口，需要允许跨域；Vite 也已配置 /api 代理
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(symbols.router, prefix="/api")
app.include_router(expiries.router, prefix="/api")
app.include_router(screen.router, prefix="/api")
app.include_router(notes.router, prefix="/api")
app.include_router(params.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(scan.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(optimize.router, prefix="/api")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
