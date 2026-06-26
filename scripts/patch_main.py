#!/usr/bin/env python3
"""Patch main.py for skill wiring."""
import ast

with open('dragon/main.py') as f:
    content = f.read()

# Patch 1: Import skills module
old_import = "from dragon.tool.builtins import register_builtins"
new_import = "from dragon.tool.builtins import register_builtins\nfrom dragon.tool.builtins import skills as _skills_module"
assert old_import in content, "Could not find import"
content = content.replace(old_import, new_import)

# Patch 2: Wire skill_engine to skills module
old_wire = '''    # Wire skill executor to dispatcher
    skill_engine.register_executor('''
assert old_wire in content, "Could not find wire point"

new_wire = '''    # Wire skill tools to skill engine
    _skills_module.set_skill_engine(skill_engine)
    logger.info("Skill tools wired to engine")

    # Wire skill executor to dispatcher
    skill_engine.register_executor('''
content = content.replace(old_wire, new_wire)

# Patch 3: Pass skill_engine and tool_registry to GatewayServer
old_gw = '''    gateway_server = GatewayServer(
        provider_registry=pr,
        session_store=ss,
        pairing_store=pairing,
        system_prompt=gw.system_prompt or (
            \"你是 Dragon Agent，一个诚实 AI 助手。\\n\"
            \"基于多模型辩论和事实核查提供可信回答。\"
        ),
    )'''
assert old_gw in content, "Could not find gateway creation"

new_gw = '''    gateway_server = GatewayServer(
        provider_registry=pr,
        session_store=ss,
        tool_registry=tool_registry,
        skill_engine=skill_engine,
        pairing_store=pairing,
        system_prompt=gw.system_prompt or "",
    )'''
content = content.replace(old_gw, new_gw)

# Validate syntax
ast.parse(content)
print("main.py: syntax OK")

with open('dragon/main.py', 'w') as f:
    f.write(content)
print("main.py: written")
