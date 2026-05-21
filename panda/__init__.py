"""
Panda Agent v1.0.0

Six-engine intelligent agent that surpasses single-model architectures
through multi-model debate, goal-backward reasoning, and persistent memory graphs.
"""

from panda.router import PandaRouter, RouteResult, RouterStatus
from panda.dispatch import PandaDispatcher, DispatchResult
from panda.guard import AntiLoopGuard, LoopAction, LoopPattern
from panda.debate import GoalBackwardEngine, GoalState, ActionNode
from panda.explorer import ExplorerEnsemble, ExploreStrategy, ExplorationResult, ExplorerConfig
from panda.jury import JuryDebate, JuryVerdict, Ballot, DebateRound, VoteDecision
from panda.memory import MemoryGraph, Entity, Relation
from panda.interrupt import InterruptManager, TaskInterrupted, get_interrupt_manager
from panda.config import PandaConfig

__version__ = "1.0.0"
__all__ = [
    "PandaRouter", "RouteResult", "RouterStatus",
    "PandaDispatcher", "DispatchResult",
    "AntiLoopGuard", "LoopAction", "LoopPattern",
    "GoalBackwardEngine", "GoalState", "ActionNode",
    "ExplorerEnsemble", "ExploreStrategy", "ExplorationResult", "ExplorerConfig",
    "JuryDebate", "JuryVerdict", "Ballot", "DebateRound", "VoteDecision",
    "MemoryGraph", "Entity", "Relation",
    "InterruptManager", "TaskInterrupted", "get_interrupt_manager",
    "PandaConfig",
]

# New in v1.1 — session, provider, compression, subagent, skill, tool, mcp
from panda.session import SessionStore, Session, SessionMessage
from panda.provider import ProviderRegistry, auto_setup_providers
from panda.compression import ContextCompressor, CompressionConfig
from panda.subagent import SubagentOrchestrator, Subagent, SubagentResult, DebateResult
from panda.skill import SkillEngine
from panda.tool import ToolRegistry

__all__.extend([
    "SessionStore", "Session", "SessionMessage",
    "ProviderRegistry", "auto_setup_providers",
    "ContextCompressor", "CompressionConfig",
    "SubagentOrchestrator", "Subagent", "SubagentResult", "DebateResult",
    "SkillEngine", "ToolRegistry",
])

# v1.2 — cron, credential pool, profiles
from panda.cron import CronScheduler, CronJob
from panda.credential import CredentialPool, CredentialManager
from panda.profile import ProfileManager, Profile

__all__.extend([
    "CronScheduler", "CronJob",
    "CredentialPool", "CredentialManager",
    "ProfileManager", "Profile",
])
