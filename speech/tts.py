"""
TTS 引擎 — 基于 edge-tts 的微软在线语音合成。

核心功能：
- 接受纯文本 + rate/pitch 参数，调用 edge-tts 合成 MP3
- 音频文件缓存到 data/audio/ 目录
- 集成播放器，合成后自动播放

edge-tts 调用微软 Azure 认知服务，支持自然发音和 SSML 格式的
语速/音调调节。在线服务，需联网使用。
"""

import hashlib
import sys
from pathlib import Path
from typing import Optional

from utils.logger import logger


class TTSEngine:
    """
    edge-tts 语音合成引擎。

    支持将纯文本合成为 MP3 音频文件，并通过内置播放器播放。

    用法：
        engine = TTSEngine(config={"voice": "zh-CN-XiaoyiNeural"})
        audio_path = await engine.synthesize("你好世界", rate="+10%", pitch="+5Hz")
        await engine.play(audio_path)
    """

    # 默认值
    DEFAULT_OUTPUT_DIR = "data/audio"
    DEFAULT_VOICE = "zh-CN-XiaoyiNeural"

    def __init__(self, config: Optional[dict] = None):
        """
        参数：
            config: TTS 配置字典，键包括：
                - voice: 语音角色名 (默认 zh-CN-XiaoyiNeural)
                - rate: 全局语速 (默认 "+0%")
                - pitch: 全局音调 (默认 "+0Hz")
                - volume: 音量倍数 (默认 1.0)
                - output_dir: 输出目录 (默认 data/audio)
                - auto_play: 是否合成后自动播放 (默认 True)
        """
        cfg = config or {}

        self._voice_name = cfg.get("voice", self.DEFAULT_VOICE)
        self._rate = cfg.get("rate", "+0%")
        self._pitch = cfg.get("pitch", "+0Hz")
        self._volume = float(cfg.get("volume", 1.0))
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
        return self._voice_name

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    # ── 合成 ──────────────────────────────────────

    async def synthesize(
        self,
        text: str,
        output_path: Optional[str] = None,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> Optional[str]:
        """
        将纯文本合成为 MP3 音频文件。

        参数：
            text: 要合成的纯文本
            output_path: 输出文件路径（可选，默认自动生成）
            voice: 语音角色名（可选，覆盖配置）
            rate: 语速覆盖（如 "+10%"，可选）
            pitch: 音调覆盖（如 "+5Hz"，可选）

        返回：
            音频文件的绝对路径，失败时返回 None
        """
        if not text or not text.strip():
            logger.warning("TTS 收到空文本，跳过合成")
            return None

        voice_name = voice or self._voice_name
        use_rate = rate or self._rate
        use_pitch = pitch or self._pitch

        # 确定输出路径
        if output_path:
            audio_path = Path(output_path)
        else:
            audio_path = self._make_output_path(text, voice_name)

        # 缓存命中
        if audio_path.exists() and audio_path.stat().st_size > 0:
            logger.debug(f"TTS 缓存命中：{audio_path.name}")
            return str(audio_path)

        try:
            from edge_tts import Communicate

            logger.debug(
                f"TTS 合成: voice={voice_name}, "
                f"rate={use_rate}, pitch={use_pitch}, "
                f"text={text[:50]}..."
            )

            communicate = Communicate(
                text,
                voice_name,
                rate=use_rate,
                pitch=use_pitch,
            )
            await communicate.save(str(audio_path))

            logger.info(
                f"TTS 合成完成：{audio_path.name} ({audio_path.stat().st_size} bytes)"
            )
            return str(audio_path)

        except ImportError as e:
            logger.error(f"edge-tts 依赖缺失：{e}，请执行 pip install edge-tts")
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
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> bool:
        """
        一键合成并播放。

        参数：
            text: 纯文本
            voice: 语音角色名
            rate: 语速覆盖（如 "+10%"）
            pitch: 音调覆盖（如 "+5Hz"）

        返回：
            True 表示成功
        """
        audio_path = await self.synthesize(text, voice=voice, rate=rate, pitch=pitch)
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
