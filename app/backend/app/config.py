"""应用配置。

所有环境变量以 MAXPAIN_ 开头，例如 MAXPAIN_DATABASE_URL。
未设置时使用下面的默认值，本地无需额外配置即可启动。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py 位于 app/backend/app/，parents[1] 是 app/backend，再向上一级是仓库根目录
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parents[1]


class Settings(BaseSettings):
    # env_file=".env" 在从 app/backend 目录启动时生效
    model_config = SettingsConfigDict(
        env_prefix="MAXPAIN_", env_file=".env", extra="ignore"
    )

    # 默认 SQLite 文件；以后切 Postgres 只需改这个环境变量
    database_url: str = f"sqlite:///{BACKEND_DIR / 'maxpain.db'}"
    # 历史行情直接读现有静态 JSON，不重复导入数据库
    web_data_dir: Path = PROJECT_ROOT / "web" / "data"
    # 允许的前端来源，多个用逗号分隔
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的配置转成 FastAPI CORS 中间件需要的列表。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
