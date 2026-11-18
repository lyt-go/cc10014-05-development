"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_name: str = "数仓分层治理平台"
    env: str = "local"
    port: int = 8000
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200
    # 清洗规则执行报告：单次生成超过该秒数即重试，最多重试 cleanse_report_max_retries 次
    cleanse_report_timeout_seconds: float = 5.0
    cleanse_report_max_retries: int = 2


settings = Settings()
