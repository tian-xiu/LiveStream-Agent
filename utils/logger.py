"""
统一日志模块 — 基于 loguru

特性：
- 控制台彩色输出（开发友好）
- 文件日志（按大小轮转 + 按时间保留）
- 结构化日志格式
- 全局单例，模块间共享
"""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(
    level: str = "INFO",
    rotation: str = "10 MB",
    retention: str = "7 days",
    log_dir: str | None = None,
) -> None:
    """
    初始化全局日志配置。
    
    参数：
        level: 控制台和文件最低日志级别
        rotation: 文件轮转策略（大小 / 时间）
        retention: 旧日志保留时长
        log_dir: 日志目录，默认为项目根目录下的 data/logs/
    """
    # 移除默认 handler
    logger.remove()

    # --- 控制台输出：彩色、简洁 ---
    logger.add(
        sys.stdout,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # --- 文件输出：结构化、持久化 ---
    if log_dir is None:
        # 尝试从环境变量推断项目根，否则使用 data/logs
        project_root = Path(__file__).resolve().parent.parent
        log_dir = str(project_root / "data" / "logs")

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / "agent_{time:YYYY-MM-DD}.log"

    logger.add(
        str(log_path),
        level="DEBUG",                     # 文件记录 DEBUG 以上
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "process:{process} | thread:{thread} | "
            "{name}:{function}:{line} | "
            "{message}"
        ),
        backtrace=True,
        diagnose=True,
    )

    logger.info(f"日志系统初始化完成，日志目录：{log_dir}")


# 包级单例，已导入即可用
__all__ = ["logger", "setup_logger"]
