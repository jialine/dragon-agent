"""
Dragon Agent v1.0.0

Six-engine intelligent agent that surpasses single-model architectures
through multi-model debate, goal-backward reasoning, and persistent memory graphs.
"""

from dragon.router import DragonRouter, RouteResult, RouterStatus
from dragon.dispatch import DragonDispatcher, DispatchResult
from dragon.guard import AntiLoopGuard, LoopAction, LoopPattern
from dragon.debate import GoalBackwardEngine, GoalState, ActionNode
from dragon.explorer import ExplorerEnsemble, ExploreStrategy, ExplorationResult, ExplorerConfig
from dragon.jury import JuryDebate, JuryVerdict, Ballot, DebateRound, VoteDecision
from dragon.memory import MemoryGraph, Entity, Relation
from dragon.interrupt import InterruptManager, TaskInterrupted, get_interrupt_manager
from dragon.config import DragonConfig

__version__ = "1.0.0"
__all__ = [
    "DragonRouter", "RouteResult", "RouterStatus",
    "DragonDispatcher", "DispatchResult",
    "AntiLoopGuard", "LoopAction", "LoopPattern",
    "GoalBackwardEngine", "GoalState", "ActionNode",
    "ExplorerEnsemble", "ExploreStrategy", "ExplorationResult", "ExplorerConfig",
    "JuryDebate", "JuryVerdict", "Ballot", "DebateRound", "VoteDecision",
    "MemoryGraph", "Entity", "Relation",
    "InterruptManager", "TaskInterrupted", "get_interrupt_manager",
    "DragonConfig",
]

# New in v1.1 — session, provider, compression, subagent, skill, tool, mcp
from dragon.session import SessionStore, Session, SessionMessage
from dragon.provider import ProviderRegistry, auto_setup_providers
from dragon.compression import ContextCompressor, CompressionConfig
from dragon.subagent import SubagentOrchestrator, Subagent, SubagentResult, DebateResult
from dragon.skill import SkillEngine
from dragon.tool import ToolRegistry

__all__.extend([
    "SessionStore", "Session", "SessionMessage",
    "ProviderRegistry", "auto_setup_providers",
    "ContextCompressor", "CompressionConfig",
    "SubagentOrchestrator", "Subagent", "SubagentResult", "DebateResult",
    "SkillEngine", "ToolRegistry",
])

# v1.2 — cron, credential pool, profiles
from dragon.cron import CronScheduler, CronJob
from dragon.credential import CredentialPool, CredentialManager
from dragon.profile import ProfileManager, Profile

__all__.extend([
    "CronScheduler", "CronJob",
    "CredentialPool", "CredentialManager",
    "ProfileManager", "Profile",
])

# v1.3 — deep context compressor, prompt builder, enhanced credential pool
from dragon.compressor import ContextCompressor, CompressedContext, CompressorStats, \
    CompressionStrategy, TokenEstimator
from dragon.prompt_builder import PromptBuilder, BuiltPrompt, CachePolicy, MiniTemplate
from dragon.credential_pool import CredentialPool as CredentialPoolV2, \
    CredentialManager as CredentialManagerV2, Credential, CredentialStatus, PoolStats

__all__.extend([
    "ContextCompressor", "CompressedContext", "CompressorStats",
    "CompressionStrategy", "TokenEstimator",
    "PromptBuilder", "BuiltPrompt", "CachePolicy", "MiniTemplate",
    "CredentialPoolV2", "CredentialManagerV2", "Credential",
    "CredentialStatus", "PoolStats",
])

# v1.4 — guardrails, rate limiter, think scrubber, redaction
from dragon.tool.guardrails import ToolGuardrails, GuardrailConfig, GuardrailCheck
from dragon.rate_limiter import RateLimiter, RateLimitConfig, RateLimitStats, get_rate_limiter
from dragon.think_scrubber import strip_think_blocks, StreamingThinkScrubber, ThinkScrubberConfig
from dragon.redact import redact_sensitive_text, RedactingFormatter, IndustryRules

__all__.extend([
    "ToolGuardrails", "GuardrailConfig", "GuardrailCheck",
    "RateLimiter", "RateLimitConfig", "RateLimitStats", "get_rate_limiter",
    "strip_think_blocks", "StreamingThinkScrubber", "ThinkScrubberConfig",
    "redact_sensitive_text", "RedactingFormatter", "IndustryRules",
])

# v1.5 — plugin system, auxiliary client, error classifier
from dragon.plugin import PluginManager, PluginManifest, PluginContext, LoadedPlugin, PluginKind, PluginState
from dragon.plugin.loader import PluginLoader
from dragon.plugin.hooks import HookSystem, VALID_HOOKS, BeforeRequestPayload, AfterResponsePayload, ErrorPayload
from dragon.auxiliary import (
    AuxiliaryClient, ModelSlot, RouteResult as AuxRouteResult,
    RoutingStrategy, LatencyMetrics, create_dispatch_chain,
)
from dragon.error_classifier import (
    classify_api_error, ClassifiedError, ErrorCategory,
    is_retryable, get_recovery_action, format_chinese_error,
)

# v1.7 — insights, title generator, file safety, usage pricing, i18n
from dragon.insights import (
    InsightsEngine, UsageRecord, DailyRollup, WeeklyRollup, MonthlyRollup,
    create_tracking_hook, format_tokens, format_cost,
)
from dragon.title_generator import TitleGenerator, generate_title
from dragon.file_safety import (
    SafetyValidator, SafePath, PathRejection,
    create_default_validator, quick_check_read, quick_check_write,
    is_file_extension_safe, sanitize_filename,
)
from dragon.usage_pricing import (
    ModelPricing, CostResult,
    get_pricing, get_cost, list_models, list_providers,
    get_provider_models, convert_currency, format_cost as format_pricing_cost,
    estimate_session_cost, has_known_pricing,
)
from dragon.i18n import (
    t, get_locale, set_locale, reset_locale,
    add_translations, get_translations,
    SUPPORTED_LOCALES, DEFAULT_LOCALE,
)

__all__.extend([
    "InsightsEngine", "UsageRecord", "DailyRollup", "WeeklyRollup", "MonthlyRollup",
    "create_tracking_hook", "format_tokens", "format_cost",
    "TitleGenerator", "generate_title",
    "SafetyValidator", "SafePath", "PathRejection",
    "create_default_validator", "quick_check_read", "quick_check_write",
    "is_file_extension_safe", "sanitize_filename",
    "ModelPricing", "CostResult",
    "get_pricing", "get_cost", "list_models", "list_providers",
    "get_provider_models", "convert_currency", "format_pricing_cost",
    "estimate_session_cost", "has_known_pricing",
    "t", "get_locale", "set_locale", "reset_locale",
    "add_translations", "get_translations",
    "SUPPORTED_LOCALES", "DEFAULT_LOCALE",
])

# v1.8 — honest AI: fact checking, consensus, web search, hallucination tracking
from dragon.factcheck import FactChecker, FactClaim, VerificationResult, ClaimType, VerificationStatus
from dragon.consensus import ConsensusBuilder, ConsensusResult, SourceTracker
from dragon.web_search import WebSearcher, SearchResult
from dragon.web_providers import WebSearchRouter, WebSearchResult as ProviderSearchResult, SearchProvider, BraveSearchProvider, SearXNGSProvider, DuckDuckGoProvider
from dragon.hallmetrics import HallucinationTracker, HallucinationReport

__all__.extend([
    "FactChecker", "FactClaim", "VerificationResult", "ClaimType", "VerificationStatus",
    "ConsensusBuilder", "ConsensusResult", "SourceTracker",
    "WebSearcher", "SearchResult",
    "WebSearchRouter", "ProviderSearchResult", "SearchProvider",
    "BraveSearchProvider", "SearXNGSProvider", "DuckDuckGoProvider",
    "HallucinationTracker", "HallucinationReport",
])

# v1.9 — API layer: auth, billing, API keys
from dragon.api import create_app, init_db, get_db, get_session
from dragon.api.models import User, ApiKey, Subscription, PaymentOrder, UsageLog
from dragon.confidence import ConfidenceCalibrator, CalibrationResult, CalibrationStats

__all__.extend([
    "create_app", "init_db", "get_db", "get_session",
    "User", "ApiKey", "Subscription", "PaymentOrder", "UsageLog",
    "ConfidenceCalibrator", "CalibrationResult", "CalibrationStats",
])
