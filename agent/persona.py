"""
人设管理 — 加载、存储和组装 Agent 人格。

功能：
- 从 YAML 文件加载人设定义
- 构建带人设的 System Prompt（Jinja2 风格变量替换）
- 支持运行时切换人设
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from utils.logger import logger


# ── 人设数据模型 ───────────────────────────────

@dataclass
class Persona:
    """Agent 人设定义。"""
    name: str = "小Q"
    description: str = ""
    traits: list[str] = field(default_factory=list)
    speaking_style: list[str] = field(default_factory=list)
    catchphrases: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    greeting: str = "warm"                       # warm / casual / energetic
    humor_level: float = 0.5
    talkativeness: float = 0.5

    def to_text(self) -> str:
        """将人设转换为自然语言描述（用于 prompt）。"""
        parts = [f"你的名字是{self.name}。{self.description}"]

        if self.traits:
            parts.append("你的性格特点：" + "、".join(self.traits) + "。")

        if self.speaking_style:
            parts.append("说话风格：" + "；".join(self.speaking_style) + "。")

        if self.catchphrases:
            parts.append("常用口头禅：" + "、".join(self.catchphrases) + "。")

        if self.rules:
            parts.append("行为准则：" + "；".join(self.rules) + "。")

        return "\n".join(parts)


# ── 人设加载器 ────────────────────────────────

class PersonaManager:
    """
    人设管理器。

    使用方式：
        mgr = PersonaManager("config/personas")
        persona = mgr.load("default")
        prompt = mgr.build_system_prompt(persona, context)
    """

    def __init__(self, personas_dir: str = "config/personas"):
        self._personas_dir = Path(personas_dir)
        self._cache: dict[str, Persona] = {}

    def load(self, name: str) -> Persona:
        """
        加载指定名称的人设。

        参数：
            name: 人设文件名（不含 .yaml 扩展名）

        返回：
            Persona 对象
        """
        # 缓存命中
        if name in self._cache:
            return self._cache[name]

        file_path = self._personas_dir / f"{name}.yaml"
        if not file_path.exists():
            logger.warning(f"人设文件不存在：{file_path}，使用内置默认人设")
            return self._get_default()

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            persona = self._parse_persona(data)
            self._cache[name] = persona
            logger.info(f"加载人设：{persona.name} ({file_path})")
            return persona

        except Exception as e:
            logger.error(f"加载人设失败：{e}，使用默认人设")
            return self._get_default()

    def build_system_prompt(
        self,
        persona: Persona,
        recent_messages: str = "",
        user_profile: str = "",
        long_term_memories: str = "",
        prompt_template_path: str = "config/prompts/system.yaml",
    ) -> str:
        """
        构建完整的 System Prompt。

        加载 prompt 模板，用人设和上下文变量填充。

        参数：
            persona: 已加载的人设对象
            recent_messages: 近期对话文本（格式化后）
            user_profile: 当前用户画像文本
            prompt_template_path: 模板文件路径

        返回：
            填充完成的 System Prompt 字符串
        """
        # 读取模板
        template_path = Path(prompt_template_path)
        if template_path.exists():
            with open(template_path, "r", encoding="utf-8") as f:
                template_data = yaml.safe_load(f)
            template = template_data.get("system_prompt", "")
        else:
            logger.warning(f"Prompt 模板不存在：{template_path}，使用内置模板")
            template = self._get_default_template()

        # 变量替换
        prompt = template.replace("{{ persona_description }}", persona.to_text())
        prompt = prompt.replace("{{ persona_traits }}", "、".join(persona.traits))
        prompt = prompt.replace("{{ speaking_style }}", "；".join(persona.speaking_style))
        prompt = prompt.replace(
            "{{ catchphrases }}",
            "、".join(persona.catchphrases) if persona.catchphrases else "无特定口头禅",
        )
        prompt = prompt.replace("{{ rules }}", "；".join(persona.rules))
        prompt = prompt.replace("{{ recent_messages }}", recent_messages or "（暂无对话记录）")
        prompt = prompt.replace("{{ user_profile }}", user_profile or "（新观众，暂无信息）")
        prompt = prompt.replace("{{ long_term_memories }}", long_term_memories or "（暂无记忆）")

        return prompt

    def clear_cache(self) -> None:
        """清空人设缓存（用于热加载）。"""
        self._cache.clear()
        logger.info("人设缓存已清空")

    # ── 内部方法 ────────────────────────────────

    def _parse_persona(self, data: dict[str, Any]) -> Persona:
        """从 YAML 数据解析人设。"""
        interaction = data.get("interaction_style", {})
        personality = data.get("personality", {})

        return Persona(
            name=data.get("name", "小Q"),
            description=data.get("description", ""),
            traits=personality.get("traits", []),
            speaking_style=personality.get("speaking_style", []),
            catchphrases=personality.get("catchphrases", []),
            rules=data.get("rules", []),
            greeting=interaction.get("greeting", "warm"),
            humor_level=float(interaction.get("humor_level", 0.5)),
            talkativeness=float(interaction.get("talkativeness", 0.5)),
        )

    @staticmethod
    def _get_default() -> Persona:
        """内置默认人设（兜底）。"""
        return Persona(
            name="小Q",
            description="一个活泼可爱的虚拟主播",
            traits=["活泼开朗", "幽默风趣", "共情能力强"],
            speaking_style=["语气轻快", "善于反问"],
            catchphrases=["欢迎来到直播间呀~"],
            rules=["不透露AI身份", "不讨论敏感话题"],
        )

    @staticmethod
    def _get_default_template() -> str:
        """内置默认 Prompt 模板（兜底）。"""
        return (
            "你是一个虚拟主播。{{ persona_description }}。"
            "性格特点：{{ persona_traits }}。"
            "说话风格：{{ speaking_style }}。"
            "行为准则：{{ rules }}。"
            "\n近期对话：{{ recent_messages }}"
            "\n当前观众：{{ user_profile }}"
            "\n必须用JSON格式回复："
            '{"content":"...","emotion":{"category":"...","intensity":0.8},"action":"reply|greet|thank_gift|ignore|question","inner_thought":"..."}'
        )
