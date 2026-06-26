#!/usr/bin/env python3
"""Apply Hermes-aligned reaction support to Dragon Feishu adapter."""
import re

with open('dragon/gateway/feishu.py', 'r') as f:
    content = f.read()

# ── Step 1: Add reaction constants after logger ──────────────────
content = content.replace(
    'logger = logging.getLogger("dragon.gateway.feishu")',
    '''logger = logging.getLogger("dragon.gateway.feishu")

# ── Processing status reactions (Hermes-aligned) ─────────────────
_FEISHU_REACTION_IN_PROGRESS = "Typing"      # while processing
_FEISHU_REACTION_FAILURE = "CrossMark"       # on failure''',
    1
)

# ── Step 2: Add reaction state to __init__ ───────────────────────
content = content.replace(
    '        # Voice mode\n        self.voice_enabled: bool = False',
    '''        # Voice mode
        self.voice_enabled: bool = False

        # Processing status reactions (Hermes-aligned)
        self._reactions_enabled: bool = True
        self._pending_processing_reactions: dict = {}  # msg_id -> reaction_id''',
    1
)

# ── Step 3: Add reaction methods before _handle_ws_event ─────────
old_handle = '    async def _handle_ws_event(self, event: Any) -> None:'
insert = '''    # ── Processing Status Reactions (Hermes-aligned) ──────────────

    async def _add_reaction(self, message_id: str, emoji_type: str) -> str:
        """Add a reaction emoji to a message. Returns reaction_id or empty."""
        if not message_id or not emoji_type:
            return ""
        token = await self._get_tenant_access_token()
        if not token:
            return ""
        try:
            url = f"{self.api_base}/im/v1/messages/{message_id}/reactions"
            body = {"reaction_type": {"emoji_type": emoji_type}}
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        rid = data.get("data", {}).get("reaction_id", "")
                        if rid:
                            logger.debug("[Feishu] Reaction %s added: %s", emoji_type, rid)
                        return rid
        except Exception:
            pass
        return ""

    async def _remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        """Remove a reaction. Returns True on success."""
        if not message_id or not reaction_id:
            return False
        token = await self._get_tenant_access_token()
        if not token:
            return False
        try:
            url = f"{self.api_base}/im/v1/messages/{message_id}/reactions/{reaction_id}"
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("code") == 0
        except Exception:
            pass
        return False

    async def on_processing_start(self, message_id: str) -> None:
        """Add Typing reaction when processing begins."""
        if not self._reactions_enabled or not message_id:
            return
        reaction_id = await self._add_reaction(message_id, _FEISHU_REACTION_IN_PROGRESS)
        if reaction_id:
            self._pending_processing_reactions[message_id] = reaction_id

    async def on_processing_complete(self, message_id: str, success: bool = True) -> None:
        """Remove Typing reaction, optionally add failure mark."""
        if not self._reactions_enabled or not message_id:
            return
        reaction_id = self._pending_processing_reactions.pop(message_id, "")
        if reaction_id:
            await self._remove_reaction(message_id, reaction_id)
        if not success:
            await self._add_reaction(message_id, _FEISHU_REACTION_FAILURE)

    async def _handle_ws_event(self, event: Any) -> None:'''

content = content.replace(old_handle, insert, 1)

# ── Step 4: Update message handler to fire reactions ─────────────
old_handler = '''                # Normal message handling
                reply = await self._message_handler(message)
                await self.send_message(reply)'''

new_handler = '''                # Normal message handling — Hermes-style reactions
                msg_id = message.message_id or ""
                asyncio.create_task(self.on_processing_start(msg_id))

                success = True
                try:
                    reply = await self._message_handler(message)
                    await self.send_message(reply)
                except Exception:
                    success = False
                    raise
                finally:
                    asyncio.create_task(self.on_processing_complete(msg_id, success))'''

content = content.replace(old_handler, new_handler, 1)

# ── Verify ───────────────────────────────────────────────────────
import ast
ast.parse(content)
print("Syntax OK")

# Count key methods
for m in ['send_message', 'upload_media', '_add_reaction', '_remove_reaction',
           'on_processing_start', 'on_processing_complete']:
    count = content.count(f'async def {m}')
    print(f"  {m}: {count}")

# Write
with open('dragon/gateway/feishu.py', 'w') as f:
    f.write(content)

print(f"Written: {len(content.splitlines())} lines")
