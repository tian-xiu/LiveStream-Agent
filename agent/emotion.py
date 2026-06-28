"""
情感引擎 — 将 LLM 情感标签映射为 TTS 语音参数（rate / pitch）。

核心功能：
- 7 种情感标签 → edge-tts rate/pitch 参数映射
- 根据 intensity（强度）动态调整参数幅度
- 文本清洗：去除 emoji、控制字符、网络噪音等 TTS 无法朗读的内容

注意：edge-tts 不支持 SSML 和 voice style（mstts:express-as），
情感差异仅通过语速（rate）和音调（pitch）体现。
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from utils.logger import logger


# ── TTS 文本清洗正则 ──────────────────────────

# 匹配 emoji 及不可朗读的 Unicode 符号（传入 TTS 前移除）
_TTS_STRIP_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"   # Emoticons
    "\U0001F300-\U0001F5FF"   # Symbols & Pictographs
    "\U0001F680-\U0001F6FF"   # Transport & Map
    "\U0001F700-\U0001F77F"   # Alchemical
    "\U0001F780-\U0001F7FF"   # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"   # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"   # Supplemental Symbols
    "\U0001FA00-\U0001FA6F"   # Chess Symbols
    "\U0001FA70-\U0001FAFF"   # Symbols Extended-A
    "\U00002702-\U000027B0"   # Dingbats
    "\U00002460-\U000024FF"   # Enclosed Alphanumerics
    "\U0001F100-\U0001F1FF"   # Enclosed Alphanumeric Supplement
    "\U0001F200-\U0001F2FF"   # Enclosed Ideographic Supplement
    "\U0001F004-\U0001F0CF"   # Playing Cards / Mahjong
    "\U0000231A-\U0000231B"   # Watch / Hourglass
    "\U000023E9-\U000023F3"   # Double-triangle etc.
    "\U000023F8-\U000023FA"   # Control symbols
    "\U00002934-\U00002935"   # Arrows
    "\U000025AA-\U000025AB"   # Squares
    "\U000025B6-\U000025C0"   # Play / reverse
    "\U000025FB-\U000025FE"   # Medium squares
    "\U00002600-\U000027BF"   # Misc Symbols
    "\U0000FE00-\U0000FE0F"   # Variation Selectors
    "\U0000200D"              # ZWJ
    "\U0000FEFF"              # BOM / ZWNBSP
    "]+",
    flags=re.UNICODE,
)

# 控制字符（TTS 引擎无法朗读，应移除）
_XML_ILLEGAL_PATTERN = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F]"
)

# TTS 噪音模式：网络用语、无意义字符组合（传入 TTS 前应移除）
# 匹配：www、2333、6666、886、xswl、yyds 等纯字母/数字缩写
# 注意：不能使用 \b，因为中文字符也是 \w 成员，中文→英文过渡处没有 \b 边界
_TTS_NOISE_PATTERN = re.compile(
    r"(?:www|233+|666+|886|xswl|yyds|awsl|kksk|gkd|nbcs|zqsg|"
    r"dddd|bdjw|u1s1|nsdd|ssfd|tcl|wsl|zqsg|jdl|dssq)",
    flags=re.IGNORECASE,
)

# 重复字符压缩：连续 3 个以上相同非 CJK/字母字符 → 保留 1 个
# 例如 "！！！" → "！"（中文 "哈哈哈" 和英文 "loooool" 保持不变，TTS 可正常朗读）
_REPEAT_CHAR_PATTERN = re.compile(r"([^\u4e00-\u9fff\u3400-\u4dbfa-zA-Z])\1{2,}")


# ── 情感参数配置 ──────────────────────────────

@dataclass
class VoiceParams:
    """语音参数（edge-tts 仅支持 rate 和 pitch）。"""
    rate: str = "+0%"               # 语速（如 "+10%"）
    pitch: str = "+0Hz"             # 音调（如 "+10Hz"）


# 情感 → 基础语音参数 映射表
# intensity 越大，参数偏离基准越多
_EMOTION_MAP: dict[str, VoiceParams] = {
    "happy":        VoiceParams(rate="+10%",  pitch="+10Hz"),
    "excited":      VoiceParams(rate="+20%",  pitch="+20Hz"),
    "calm":         VoiceParams(rate="-10%",  pitch="-5Hz"),
    "sympathetic":  VoiceParams(rate="-8%",   pitch="-5Hz"),
    "funny":        VoiceParams(rate="+15%",  pitch="+5Hz"),
    "serious":      VoiceParams(rate="-5%",   pitch="+0Hz"),
    "warm":         VoiceParams(rate="-5%",   pitch="-3Hz"),
}

# 默认参数（中性情感 / 未识别情感）
_DEFAULT_PARAMS = VoiceParams(rate="+0%", pitch="+0Hz")


# ── 引擎实现 ──────────────────────────────────

class EmotionEngine:
    """
    情感语音参数引擎。

    使用方式：
        engine = EmotionEngine()
        params = engine.get_voice_params("excited", 0.5)
        # → VoiceParams(rate="+10%", pitch="+10Hz")

    注意：edge-tts 不支持 voice style，情感差异仅通过 rate/pitch 体现。
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
            VoiceParams: 语音参数（rate, pitch）
        """
        base = _EMOTION_MAP.get(emotion)
        if base is None:
            logger.warning(f"未知情感标签 '{emotion}'，使用默认参数")
            base = _DEFAULT_PARAMS

        # 根据 intensity 缩放 rate 和 pitch
        return VoiceParams(
            rate=self._scale_percent(base.rate, intensity),
            pitch=self._scale_pitch(base.pitch, intensity),
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
    def _sanitize_for_tts(text: str) -> str:
        """
        清洗文本中的 TTS 不可朗读字符。

        流程：
        1. 剥离可能残留的 XML/SSML 标签（LLM 有时会输出标签）
        1.5 去除 [xxx] 格式的语气/动作标签（LLM 有时会自行添加，如 [调皮]）
        2. 移除 emoji / 符号 / 变体选择器
        3. 移除 XML 非法控制字符
        4. 移除网络噪音（www、2333、6666 等）
        5. 压缩重复字符（如 "哈哈哈" → "哈"）
        6. Unicode 正规化（NFC：仅规范等价重组，保留全角标点）
        7. 合并多余空白

        返回清理后的纯文本。
        """
        # 1. 剥离可能残留的 XML/SSML 标签（LLM 有时会输出标签）
        text = re.sub(r"<[^>]+>", "", text)
        # 1.5 去除 [xxx] 格式的语气/动作标签（LLM 有时会自行添加，如 [调皮]）
        text = re.sub(r"\[[^\]]+\]", "", text)
        # 2. 移除 emoji / 符号
        text = _TTS_STRIP_PATTERN.sub("", text)
        # 3. 移除控制字符
        text = _XML_ILLEGAL_PATTERN.sub("", text)
        # 4. 移除网络噪音
        text = _TTS_NOISE_PATTERN.sub("", text)
        # 5. 压缩重复字符（连续 3 个以上相同字符 → 保留 1 个）
        text = _REPEAT_CHAR_PATTERN.sub(r"\1", text)
        # 6. NFC 正规化（保留全角标点）
        text = unicodedata.normalize("NFC", text)
        # 7. 合并空白
        text = re.sub(r'\s+', ' ', text).strip()
        return text

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
