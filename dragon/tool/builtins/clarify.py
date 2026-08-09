"""
Dragon Agent — Clarify Tool (Hermes-aligned)
=============================================

Tool to ask the user a question and wait for a response.
Hermes alignment: clarify(question, choices=None).

In the Dragon/Feishu context, this tool sends the question to the user
via the dispatch log and returns a placeholder indicating the question was sent.
The actual user response is handled by the gateway's conversation loop.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger("dragon.tool.builtins.clarify")


async def tool_clarify(
    question: str,
    choices: Optional[list] = None,
) -> str:
    """Ask the user a clarifying question and return their response.

    Hermes-aligned: clarify(question, choices=None)

    In Dragon's Feishu gateway, this tool records the question to the log
    and returns a structured message. The user's reply will be captured
    by the gateway in the next conversation turn.

    Args:
        question: The question to ask the user. Be specific and concise.
        choices: Optional JSON array of pre-defined choices, e.g.
            '["Fix all errors", "Fix critical only", "Show details first"]'.

    Returns:
        JSON with the question, choices (if any), and a status message.
    """
    if not question or not question.strip():
        return json.dumps({"error": "Question cannot be empty"})

    question = question.strip()

    parsed_choices = None
    if choices:
        if isinstance(choices, list):
            parsed_choices = choices
        elif isinstance(choices, str):
            try:
                parsed_choices = json.loads(choices)
                if not isinstance(parsed_choices, list):
                    parsed_choices = [c.strip() for c in choices.split(",")]
            except json.JSONDecodeError:
                parsed_choices = [c.strip() for c in choices.split(",")]

    # Log the question for dispatch tracking
    logger.info("Clarify: asking user: %s (choices=%s)", question[:200], parsed_choices)

    result = {
        "action": "clarify",
        "question": question,
        "status": "question_sent_to_user",
        "message": "问题已发送给用户，等待用户回复...",
    }

    if parsed_choices:
        result["choices"] = parsed_choices
        result["message"] += f" 可选选项: {', '.join(str(c) for c in parsed_choices)}"

    return json.dumps(result, ensure_ascii=False)
