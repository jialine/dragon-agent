#!/usr/bin/env python3
"""Patch server.py for skill catalog injection."""
import ast

with open('dragon/gateway/server.py') as f:
    content = f.read()

# Patch 1: Update __init__ 
old_init = '''        self.system_prompt = system_prompt or (
            "你是 Dragon Agent，一个能够自我进化的 AI 助手。\\n"
            "你的技能会随着使用不断改进。回答简洁、准确、有帮助。"
        )'''
new_init = '''        self._skill_engine = skill_engine
        self.system_prompt = system_prompt or self._build_system_prompt()'''

assert old_init in content, "Could not find old __init__ block"
content = content.replace(old_init, new_init)

# Patch 2: Add _build_system_prompt and _build_skills_catalog
old_register = '    def register_adapter(self, adapter: PlatformAdapter) -> None:'
assert old_register in content, "Could not find register_adapter"

new_methods = '''    def _build_system_prompt(self) -> str:
        """Build the base system prompt with skill awareness instructions."""
        prompts = [
            "你是 Dragon Agent，一个能够自我进化的 AI 助手。",
            "",
            "## 核心能力",
            "",
            "1. **技能驱动** — 面对任何任务，主动搜索并加载相关技能。",
            "2. **自我进化** — 成功完成任务后，可以创建新技能供未来使用。",
            "3. **工具使用** — 你可以调用 search_skills、load_skill、install_skill、create_skill 等工具。",
            "",
            "## 技能使用规则",
            "",
            "- 每次收到用户消息后，先判断是否需要技能帮助。",
            "- 如果任务涉及编程、调试、部署、配置等领域，先用 search_skills 搜索相关技能。",
            "- 找到匹配的技能后，用 load_skill 加载完整内容，严格按技能指令执行。",
            "- 如果没有已有技能匹配，可以先尝试自行处理；完成后若流程通用，用 create_skill 保存为技能。",
            "- 如果技能来自 Hermes 但尚未导入，用 install_skill 安装。",
            "",
            "回答简洁、准确、有帮助。中文优先。",
        ]
        return "\\n".join(prompts)

    def _build_skills_catalog(self) -> None:
        """Inject available skills catalog into the system prompt."""
        if self._skill_engine is None:
            return

        try:
            skills = self._skill_engine.list_skills()
            if not skills:
                return

            # Build a compact catalog
            catalog_lines = [
                "",
                "## 可用技能 ({} 个)".format(len(skills)),
                "",
                "以下技能可直接使用 load_skill 加载：",
                "",
            ]

            for s in skills[:100]:
                name = s.get("name", "?")
                desc = s.get("description", "")[:80]
                tags = ", ".join(s.get("tags", [])[:4])
                sr = s.get("success_rate", 0)
                sr_str = " (成功率:{:.0%})".format(sr) if sr > 0 else ""
                catalog_lines.append("- **{}**{}: {} [{}]".format(name, sr_str, desc, tags))

            if len(skills) > 100:
                remaining = len(skills) - 100
                catalog_lines.append("")
                catalog_lines.append("... 还有 {} 个技能，用 search_skills 搜索。".format(remaining))

            catalog_lines.append("")
            catalog_lines.append('使用 search_skills(query="关键词") 搜索合适的技能。')
            catalog_lines.append('使用 load_skill(name="技能名") 加载完整内容。')

            catalog = "\\n".join(catalog_lines)
            self.system_prompt += catalog

        except Exception as e:
            import logging
            logging.getLogger("dragon.gateway.server").warning(
                "Failed to build skills catalog: %s", e
            )

    def register_adapter(self, adapter: PlatformAdapter) -> None:'''

content = content.replace(old_register, new_methods)

# Validate syntax
ast.parse(content)
print("server.py: syntax OK")

with open('dragon/gateway/server.py', 'w') as f:
    f.write(content)
print("server.py: written")
