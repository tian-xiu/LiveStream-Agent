"""
情感引擎 — 将 LLM 情感标签映射为 TTS 语音参数。

核心功能：
- 7 种情感标签 ↔ edge-tts SSML 参数的映射
- 根据 intensity（强度）动态调整参数幅度
- 生成 edge-tts 兼容的 SSML 标记
"""

from dataclasses import dataclass
from typing import Optional

from utils.logger import logger


# ── 情感参数配置 ──────────────────────────────

@dataclass
class VoiceParams:
    """语音参数。"""
    rate: str = "+0%"               # 语速（SSML prosody rate，如 "+10%"）
    pitch: str = "+0Hz"             # 音调（SSML prosody pitch，如 "+10Hz"）
    style: str = "general"          # edge-tts style：cheerful / sad / excited 等


# 情感 → 基础语音参数 映射表
# intensity 越大，参数偏离基准越多
_EMOTION_MAP: dict[str, VoiceParams] = {
    "happy":        VoiceParams(rate="+10%",  pitch="+10Hz",  style="cheerful"),
    "excited":      VoiceParams(rate="+20%",  pitch="+20Hz",  style="excited"),
    "calm":         VoiceParams(rate="-10%",  pitch="-5Hz",   style="calm"),
    "sympathetic":  VoiceParams(rate="-8%",   pitch="-5Hz",   style="empathetic"),
    "funny":        VoiceParams(rate="+15%",  pitch="+5Hz",   style="cheerful"),
    "serious":      VoiceParams(rate="-5%",   pitch="+0Hz",   style="serious"),
    "warm":         VoiceParams(rate="-5%",   pitch="-3Hz",   style="gentle"),
}

# 默认参数（中性情感 / 未识别情感）
_DEFAULT_PARAMS = VoiceParams(rate="+0%", pitch="+0Hz", style="general")


# ── 引擎实现 ──────────────────────────────────

class EmotionEngine:
    """
    情感语音参数引擎。

    使用方式：
        engine = EmotionEngine()
        ssml = engine.to_ssml("欢迎来到直播间！", "happy", 0.8)
        # → <speak><prosody rate="+10%" pitch="+10Hz">欢迎...</prosody></speak>

        params = engine.get_voice_params("excited", 0.5)
        # → VoiceParams(rate="+20%", pitch="+20Hz", style="excited")
    """

    # 支持的情感标签
    VALID_EMOTIONS = frozenset(_EMOTION_MAP.keys())

    @classmethod
    def is_valid(cls, emotion: str) -> bool:
        """检查情感标签是否合法。"""
        return emotion in cls.VALID_EMOTIONS

    def get_voice_params(self, emotion: str, intensity: float = 0.5) -> VoiceParams:
        """
        根据情感和强度获取语音参数。

        参数：
            emotion: 情感标签（happy / excited / calm / sympathetic / funny / serious / warm）
            intensity: 情感强度 0.0 ~ 1.0

        返回：
            VoiceParams: 语音参数（rate, pitch, style）
        """
        base = _EMOTION_MAP.get(emotion)
        if base is None:
            logger.warning(f"未知情感标签 '{emotion}'，使用默认参数")
            base = _DEFAULT_PARAMS

        # 根据 intensity 缩放 rate 和 pitch
        return VoiceParams(
            rate=self._scale_percent(base.rate, intensity),
            pitch=self._scale_pitch(base.pitch, intensity),
            style=base.style,
        )

    def to_ssml(
        self,
        text: str,
        emotion: str,
        intensity: float = 0.5,
        global_rate: str = "+0%",
        global_pitch: str = "+0Hz",
    ) -> str:
        """
        生成带情感参数的 SSML 文本。

        参数：
            text: 要合成的文本
            emotion: 情感标签
            intensity: 情感强度
            global_rate: 全局语速叠加（来自配置文件）
            global_pitch: 全局音调叠加

        返回：
            SSML 字符串，可直接传给 edge-tts
        """
        params = self.get_voice_params(emotion, intensity)

        # 合并全局参数和情感参数
        final_rate = self._add_percent(global_rate, params.rate)
        final_pitch = self._add_pitch(global_pitch, params.pitch)

        # 转义 XML 特殊字符
        text = self._escape_xml(text)

        return (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="zh-CN">'
            f'<voice name="zh-CN-XiaoyiNeural">'
            f'<mstts:express-as style="{params.style}">'
            f'<prosody rate="{final_rate}" pitch="{final_pitch}">'
            f"{text}"
            f"</prosody>"
            f"</mstts:express-as>"
            f"</voice>"
            f"</speak>"
        )

    def find_best_emotion(self, keywords: str) -> str:
        """
        从文本关键词中推测最佳情感标签（简单规则匹配）。

        用于 LLM 未返回情感标签时的降级处理。
        """
        text_lower = keywords.lower()
        if any(kw in text_lower for kw in ["哈哈", "笑", "有趣", "搞笑", "哈哈哈"]):
            return "funny"
        if any(kw in text_lower for kw in ["欢迎", "新朋友", "来了", "大家好"]):
            return "happy"
        if any(kw in text_lower for kw in ["谢谢", "感谢", "礼物", "支持"]):
            return "warm"
        if any(kw in text_lower for kw in ["伤心", "难过", "不开心", "哭"]):
            return "sympathetic"
        if any(kw in text_lower for kw in ["厉害", "牛", "666", "太强"]):
            return "excited"
        return "calm"

    # ── 内部工具方法 ────────────────────────────

    @staticmethod
    def _scale_percent(value: str, intensity: float) -> str:
        """按强度缩放百分比值（如 "+10%" → "+5%" 当 intensity=0.5）。"""
        sign = "+" if value.startswith("+") else "-"
        num = int(value[1:].replace("%", ""))
        scaled = round(num * intensity)
        return f"{sign}{scaled}%"

    @staticmethod
    def _scale_pitch(value: str, intensity: float) -> str:
        """按强度缩放音调值（如 "+20Hz" → "+10Hz" 当 intensity=0.5）。"""
        sign = "+" if value.startswith("+") else "-"
        num = int(value[1:].replace("Hz", ""))
        scaled = round(num * intensity)
        return f"{sign}{scaled}Hz"

    @staticmethod
    def _add_percent(a: str, b: str) -> str:
        """合并两个百分比值。"""
        na = int(a.replace("%", ""))
        nb = int(b.replace("%", ""))
        total = na + nb
        sign = "+" if total >= 0 else ""
        return f"{sign}{total}%"

    @staticmethod
    def _add_pitch(a: str, b: str) -> str:
        """合并两个音调值。"""
        na = int(a.replace("Hz", ""))
        nb = int(b.replace("Hz", ""))
        total = na + nb
        sign = "+" if total >= 0 else ""
        return f"{sign}{total}Hz"

    @staticmethod
    def _escape_xml(text: str) -> str:
        """转义 XML 特殊字符。"""
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
        )
