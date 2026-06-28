"""
LiveStream-Agent 主入口
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
虚拟主播 Agent：接收弹幕 → AI 决策 → 情感语音播报

用法：
    python main.py <房间URL或ID> [--platform douyin|bilibili] [--config config/settings.yaml] [--no-voice]

示例：
    # 抖音直播间（完整 URL）
    python main.py https://live.douyin.com/361749035935

    # 抖音直播间（仅 ID）
    python main.py 361749035935 --platform douyin

    # B站直播间
    python main.py 23564688 --platform bilibili

    # 仅文本回复，不播放语音
    python main.py 361749035935 --no-voice

生命周期：
    启动 → 连接直播间 → 开播欢迎语 → 消息循环处理 → Ctrl+C → 总结 & 退出
"""

# Windows SSL 证书存储补丁（修复某些 Windows 系统上的 ASN1 错误）
import ssl
_original_load_verify = ssl.SSLContext.load_verify_locations
def _patched_load_verify(self, cafile=None, capath=None, cadata=None):
    try:
        return _original_load_verify(self, cafile, capath, cadata)
    except ssl.SSLError:
        pass
ssl.SSLContext.load_verify_locations = _patched_load_verify

import argparse
import asyncio
import re
import sys
from pathlib import Path

import yaml


async def main() -> None:
    # ── 0. 命令行参数 ────────────────────────────────────────
    parser = argparse.ArgumentParser(
        prog="livestream-agent",
        description="LiveStream-Agent：虚拟主播 AI 助手，接收弹幕并生成情感语音回复",
    )
    parser.add_argument(
        "room_url_or_id",
        help="直播间 URL（抖音）或房间 ID（B站数字）",
    )
    parser.add_argument(
        "--platform",
        choices=["douyin", "bilibili"],
        default="douyin",
        help="直播平台（默认：douyin）",
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="配置文件路径（默认：config/settings.yaml）",
    )
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="禁用语音播报，仅输出文本日志",
    )
    args = parser.parse_args()

    # ── 1. 项目根目录 ───────────────────────────────────────
    project_root = Path(__file__).resolve().parent

    # ── 2. 加载配置 ─────────────────────────────────────────
    config_path = (project_root / args.config).resolve()
    if not config_path.exists():
        print(f"✗ 配置文件不存在：{config_path}", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # ── 3. 初始化日志 ───────────────────────────────────────
    log_cfg = config.get("logging", {})
    from utils.logger import setup_logger

    setup_logger(
        level=log_cfg.get("level", "INFO"),
        rotation=log_cfg.get("rotation", "10 MB"),
        retention=log_cfg.get("retention", "7 days"),
        log_dir=str(project_root / "data" / "logs"),
    )
    from utils.logger import logger

    logger.info("=" * 50)
    logger.info("LiveStream-Agent 启动中...")
    logger.info(f"  平台: {args.platform}")
    logger.info(f"  配置: {config_path}")

    # ── 4. 数据库 ───────────────────────────────────────────
    from storage.database import get_database

    db = get_database(str(project_root / config["memory"]["db_path"]))
    logger.info(f"  数据库: {db.db_path}")

    # ── 5. 记忆系统（三级记忆）──────────────────────────────
    from agent.memory import MemoryManager

    memory = MemoryManager(
        db,
        short_term_size=config["memory"].get("short_term_size", 20),
    )

    # ── 6. 人设管理器 ───────────────────────────────────────
    from agent.persona import PersonaManager

    persona_mgr = PersonaManager(str(project_root / "config" / "personas"))

    # ── 7. LLM 适配器 ───────────────────────────────────────
    from llm.adapter import create_adapter_from_config

    llm_adapter = create_adapter_from_config(config["llm"])
    logger.info(f"  LLM: {llm_adapter.provider_name} / {llm_adapter.model_name}")

    # ── 8. Agent 大脑 ───────────────────────────────────────
    from agent.brain import AgentBrain

    brain = AgentBrain(
        llm_adapter=llm_adapter,
        memory=memory,
        persona_mgr=persona_mgr,
        config=config.get("agent"),
    )

    # ── 9. 情感引擎 ─────────────────────────────────────────
    from agent.emotion import EmotionEngine

    emotion = EmotionEngine()

    # ── 10. TTS 引擎 ────────────────────────────────────────
    from speech.tts import create_tts_from_config

    tts = create_tts_from_config(config)

    # ── 11. 平台连接器 ──────────────────────────────────────
    if args.platform == "douyin":
        from connectors.douyin.connector import DouyinConnector

        connector = DouyinConnector()
        # 如果是纯数字 ID，补全为完整 URL
        connect_target = args.room_url_or_id
        if not connect_target.startswith("http"):
            connect_target = f"https://live.douyin.com/{connect_target}"
    else:
        from connectors.bilibili.connector import BilibiliConnector

        connector = BilibiliConnector()
        # 如果是 B站 URL，提取纯数字房间 ID
        connect_target = args.room_url_or_id
        if "bilibili.com" in connect_target:
            match = re.search(r"(\d+)", connect_target)
            if match:
                connect_target = match.group(1)

    voice_enabled = not args.no_voice

    # ── 12. 消息过滤器 ──────────────────────────────────────
    from pipeline import create_filter_from_config

    msg_filter = create_filter_from_config(config)

    # ── 13. 响应调度器 ──────────────────────────────────────
    from pipeline.scheduler import create_scheduler_from_config

    scheduler = create_scheduler_from_config(config)

    # ── 14. 字幕窗口 ────────────────────────────────────────
    from ui import SubtitleOverlay, DanmakuFeed

    subtitle = SubtitleOverlay()
    subtitle.start()

    danmaku_feed = DanmakuFeed()
    danmaku_feed.start()

    # ── 15. 管道编排器 ──────────────────────────────────────
    from pipeline import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(
        connector=connector,
        brain=brain,
        emotion=emotion,
        tts=tts,
        msg_filter=msg_filter,
        scheduler=scheduler,
        voice_enabled=voice_enabled,
        subtitle=subtitle,
        danmaku_feed=danmaku_feed,
    )

    # ── 16. 启动 ────────────────────────────────────────────
    try:
        # 开始数据库会话
        session_id = await memory.start_session(
            room_id=connect_target,
            platform=args.platform,
        )
        logger.info(f"  会话 ID: {session_id}")

        # 连接直播间
        success = await connector.connect(connect_target)
        if not success:
            logger.error("无法连接到直播间，退出")
            return

        # 开播欢迎语
        if voice_enabled:
            greeting = await brain.greet()
            if greeting and greeting.should_reply():
                # 获取情感语音参数
                voice_params = emotion.get_voice_params(
                    emotion=greeting.emotion.category,
                    intensity=greeting.emotion.intensity,
                )
                # 清洗文本（移除 emoji、噪音、XML 标签等）
                clean_text = emotion._sanitize_for_tts(greeting.content)
                if clean_text:
                    # 显示字幕
                    if subtitle:
                        subtitle.show(
                            text=clean_text,
                            nickname="",
                            action="greet",
                        )
                        await asyncio.sleep(0.1)
                    # 使用纯文本 + 参数合成
                    await tts.speak(
                        text=clean_text,
                        rate=voice_params.rate,
                        pitch=voice_params.pitch,
                    )

        # 启动管道处理
        await orchestrator.start()
        logger.info("✦ LiveStream-Agent 运行中，按 Ctrl+C 退出 ✦")

        # 保持运行，直到用户中断
        while True:
            await asyncio.sleep(1)

    except asyncio.CancelledError:
        logger.info("收到退出信号 (CancelledError)")
    except Exception as e:
        logger.error(f"运行时错误：{e}")
        raise
    finally:
        # ── 17. 优雅退出 ─────────────────────────────────────
        logger.info("正在关闭...")

        # 停止字幕窗口
        subtitle.stop()

        # 停止弹幕字幕窗口
        danmaku_feed.stop()

        # 停止管道处理
        await orchestrator.stop()

        # 生成直播总结
        summary = ""
        try:
            summary = await brain.summarize_session()
            logger.info(f"直播总结：{summary}")
        except Exception as e:
            logger.warning(f"生成总结失败：{e}")

        # 结束数据库会话（写入 ended_at + summary）
        await brain.end_session(summary)

        # 关闭数据库连接
        await db.close()

        logger.info("LiveStream-Agent 已安全退出")
        logger.info(f"运行统计：{orchestrator.stats}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n直播已结束，感谢使用 LiveStream-Agent！")
