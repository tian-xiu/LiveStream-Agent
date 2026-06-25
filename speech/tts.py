"""
TTS 引擎 — 基于 edge-tts 的情感化语音合成。

核心功能：
- 接受 EmotionEngine 生成的 SSML 文本，调用 edge-tts 合成 MP3
- 支持自定义语速、音调、音量参数
- 音频文件缓存到 data/audio/ 目录
- 集成播放器，合成后自动播放
"""

import asyncio
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from utils.logger import logger


class TTSEngine:
    """
    edge-tts 语音合成引擎。

    支持将 SSML 或纯文本合成为 MP3 音频文件，
    并通过内置播放器播放。

    用法：
        engine = TTSEngine(config={"voice": "zh-CN-XiaoyiNeural"})
        audio_path = await engine.synthesize(ssml_text)
        await engine.play(audio_path)
    """

    # 默认输出目录（相对于项目根目录）
    DEFAULT_OUTPUT_DIR = "data/audio"

    def __init__(self, config: Optional[dict] = None):
        """
        参数：
            config: TTS 配置字典，键包括：
                - voice: 语音角色 (默认 zh-CN-XiaoyiNeural)
                - rate: 全局语速 (默认 "+0%")
                - pitch: 全局音调 (默认 "+0Hz")
                - volume: 音量 (默认 "+0%")
                - output_dir: 输出目录 (默认 data/audio)
                - auto_play: 是否合成后自动播放 (默认 True)
        """
        cfg = config or {}

        self._voice = cfg.get("voice", "zh-CN-XiaoyiNeural")
        self._rate = cfg.get("rate", "+0%")
        self._pitch = cfg.get("pitch", "+0Hz")
        self._volume = cfg.get("volume", "+0%")
        self._auto_play = cfg.get("auto_play", True)

        # 确定项目根目录（speech/ 的上两级）
        self._project_root = Path(__file__).resolve().parent.parent
        self._output_dir = self._project_root / cfg.get(
            "output_dir", self.DEFAULT_OUTPUT_DIR
        )

        # 确保输出目录存在
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._player: Optional["AudioPlayer"] = None

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    # ── 合成 ──────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        output_path: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> Optional[str]:
        """
        将文本（SSML 或纯文本）合成为 MP3 音频文件。

        参数：
            text: 要合成的文本（支持 SSML）
            output_path: 输出文件路径（可选，默认自动生成）
            voice: 语音角色（可选，覆盖配置）

        返回：
            音频文件的绝对路径，失败时返回 None
        """
        if not text or not text.strip():
            logger.warning("TTS 收到空文本，跳过合成")
            return None

        voice_name = voice or self._voice

        # 确定输出路径
        if output_path:
            audio_path = Path(output_path)
        else:
            audio_path = self._make_output_path(text, voice_name)

        # 如果文件已存在（相同内容的缓存），直接返回
        if audio_path.exists() and audio_path.stat().st_size > 0:
            logger.debug(f"TTS 缓存命中：{audio_path.name}")
            return str(audio_path)

        try:
            import edge_tts

            # SSML 文本中可能已包含 voice 和 prosody 标签，
            # edge-tts 接受 SSML 字符串，但不会覆盖我们传入的 voice 参数。
            # 策略：如果 text 本身是 SSML，直接传；否则用 plain text + 参数。
            is_ssml = text.strip().startswith("<speak")

            if is_ssml:
                # SSML 文本：edge-tts 会解析 SSML 中的 voice/prosody 设置
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice_name,
                    rate=self._rate,
                    volume=self._volume,
                )
            else:
                # 纯文本：使用全局参数
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice_name,
                    rate=self._rate,
                    pitch=self._pitch,
                    volume=self._volume,
                )

            await communicate.save(str(audio_path))

            logger.info(f"TTS 合成完成：{audio_path.name} ({audio_path.stat().st_size} bytes)")
            return str(audio_path)

        except ImportError:
            logger.error("edge-tts 未安装，请执行：pip install edge-tts")
            return None
        except Exception as e:
            logger.error(f"TTS 合成失败：{e}")
            return None

    # ── 播放 ──────────────────────────────────────

    async def play(self, audio_path: str) -> bool:
        """
        播放音频文件。

        参数：
            audio_path: 音频文件路径

        返回：
            True 表示播放成功
        """
        if not audio_path or not Path(audio_path).exists():
            logger.error(f"音频文件不存在：{audio_path}")
            return False

        try:
            # 延迟导入，避免循环依赖
            from speech.player import AudioPlayer

            player = AudioPlayer()
            return await player.play(audio_path)

        except ImportError as e:
            logger.error(f"无法加载播放器：{e}")
            return False
        except Exception as e:
            logger.error(f"播放失败：{e}")
            return False

    # 便捷方法：合成并播放
    async def speak(
        self,
        text: str,
        voice: Optional[str] = None,
    ) -> bool:
        """
        一键合成并播放。

        参数：
            text: SSML 或纯文本
            voice: 语音角色

        返回：
            True 表示成功
        """
        audio_path = await self.synthesize(text, voice=voice)
        if audio_path and self._auto_play:
            return await self.play(audio_path)
        return audio_path is not None

    # ── 内部方法 ──────────────────────────────────

    def _make_output_path(self, text: str, voice: str) -> Path:
        """
        根据文本内容生成唯一的输出文件路径（用于缓存去重）。

        对文本内容取 MD5，文件名格式：{voice}_{md5[:12]}.mp3
        """
        text_bytes = (voice + text).encode("utf-8")
        file_hash = hashlib.md5(text_bytes).hexdigest()[:12]
        return self._output_dir / f"{voice}_{file_hash}.mp3"


# ── TTS 工厂函数 ──────────────────────────────────


def create_tts_from_config(config: dict) -> TTSEngine:
    """从配置字典创建 TTS 引擎实例。"""
    return TTSEngine(config=config.get("tts", {}))
