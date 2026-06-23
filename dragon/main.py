"""
Dragon Agent API Server.

FastAPI entry point with:
- /v1/chat — full conversation with routing + dispatch
- /v1/chat/stream — SSE streaming
- /health — component health checks
"""

import time
import logging
import json
import asyncio
import base64
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from dragon.config import DragonConfig
from dragon.router import DragonRouter, RouteResult, RouterStatus as _ignore_RouterStatus
from dragon.dispatch import DragonDispatcher, ProviderProfile, DispatchResult as _ignore_DispatchResult
from dragon.guard import AntiLoopGuard, LoopAction as _ignore_LoopAction
from dragon.interrupt import get_interrupt_manager, TaskInterrupted, InterruptManager
from dragon.backup import DragonBackup, BackupLockError, BackupUploadError, BackupRestoreError, BackupNotFoundError
from dragon.consult import ExpertConsultation, ConsultationAssessment, ConsultationResult
from dragon.skill import SkillEngine
from dragon.tool import ToolRegistry
from dragon.tool.builtins import register_builtins

from dragon.jury import JuryDebate, JuryVerdict
from dragon.factcheck import FactChecker, ClaimExtractor
from dragon.consensus import ConsensusBuilder, ConsensusResult
from dragon.hallmetrics import HallucinationTracker, HallucinationReport
from dragon.web_search import WebSearcher

# Gateway
from dragon.gateway import GatewayServer
from dragon.gateway.feishu import FeishuAdapter
from dragon.gateway.wechat import WeChatAdapter
from dragon.gateway.telegram import TelegramAdapter
from dragon.gateway.discord import DiscordAdapter

logger = logging.getLogger("dragon.api")

# ── Globals ──────────────────────────────────────────────
router: Optional[DragonRouter] = None
dispatcher: Optional[DragonDispatcher] = None
guard: Optional[AntiLoopGuard] = None
config: Optional[DragonConfig] = None
skill_engine: Optional[SkillEngine] = None
tool_registry: Optional[ToolRegistry] = None

# Honest AI pipeline globals
jury: Optional[JuryDebate] = None
fact_checker: Optional[FactChecker] = None
consensus_builder: Optional[ConsensusBuilder] = None
hall_tracker: Optional[HallucinationTracker] = None

# Gateway
gateway_server: Optional[GatewayServer] = None
_gateway_task: Optional[asyncio.Task] = None
_gateway_adapters: list = []  # track adapters for connect/disconnect


# ── Lifespan ─────────────────────────────────────────────
async def _start_gateway(cfg) -> bool:
    """Start GatewayServer based on DragonConfig.gateway.

    Returns True if gateway was started (standalone or mounted).
    """
    global gateway_server, _gateway_task, _gateway_adapters

    gw = cfg.gateway
    if not gw.enabled or not gw.platforms:
        logger.info("Gateway disabled (no platforms configured)")
        return False

    # Build provider registry for gateway (reuse dispatcher)

    class _GwProviderRegistry:
        async def call(self, provider_name, messages, max_tokens=2048, **kwargs):
            if not dispatcher:
                raise RuntimeError("dispatcher not initialized")
            result = await dispatcher.dispatch(
                industry="general",
                messages=messages,
                max_tokens=max_tokens,
            )
            from types import SimpleNamespace
            return SimpleNamespace(content=result.content)

    pr = _GwProviderRegistry()

    # Simple in-memory session store (for gateway)
    class _MemSessionStore:
        def __init__(self):
            self._sessions: Dict[str, Any] = {}

        def get(self, session_id: str):
            return self._sessions.get(session_id)

        def create(self, title: str = "", platform: str = ""):
            from types import SimpleNamespace
            sid = f"sess_{len(self._sessions) + 1:06d}"
            sess = SimpleNamespace(id=sid, messages=[])
            self._sessions[sid] = sess
            return sess

        def get_messages(self, session_id: str, limit: int = 50):
            sess = self._sessions.get(session_id)
            if sess:
                return sess.messages[-limit:]
            return []

        def add_message(self, session_id: str, role: str, content: str):
            from types import SimpleNamespace
            sess = self._sessions.get(session_id)
            if sess:
                sess.messages.append(SimpleNamespace(role=role, content=content))

    from dragon.gateway.pairing import PairingStore

    ss = _MemSessionStore()
    pairing = PairingStore()

    gateway_server = GatewayServer(
        provider_registry=pr,
        session_store=ss,
        pairing_store=pairing,
        system_prompt=gw.system_prompt or (
            "你是 Dragon Agent，一个诚实 AI 助手。\n"
            "基于多模型辩论和事实核查提供可信回答。"
        ),
    )

    # Platform adapter mapping
    _ADAPTER_MAP = {
        "feishu": lambda p: FeishuAdapter(
            app_id=p.app_id, app_secret=p.app_secret,
            verification_token=p.verification_token, domain=p.domain,
            connection_mode=p.connection_mode,
        ),
        "wechat": lambda p: WeChatAdapter(
            token=p.token, app_id=p.app_id, app_secret=p.app_secret,
            encoding_aes_key=p.encoding_aes_key,
        ),
        "telegram": lambda p: TelegramAdapter(bot_token=p.bot_token),
        "discord": lambda p: DiscordAdapter(bot_token=p.bot_token),
    }

    registered = 0
    _gateway_adapters.clear()
    for plat_name, plat_cfg in gw.platforms.items():
        if not plat_cfg.enabled:
            continue
        factory = _ADAPTER_MAP.get(plat_name)
        if factory is None:
            logger.warning("Unknown platform '%s', skipping", plat_name)
            continue
        try:
            adapter = factory(plat_cfg)
            gateway_server.register_adapter(adapter)
            _gateway_adapters.append(adapter)
            registered += 1
            logger.info("Gateway platform '%s' registered", plat_name)
        except Exception as e:
            logger.error("Failed to register platform '%s': %s", plat_name, e)

    if registered == 0:
        logger.warning("No gateway platforms registered (all disabled or failed)")
        return False

    if gw.standalone:
        # Start on separate port via background task
        import uvicorn

        gateway_app = gateway_server.app
        gateway_app.title = "Dragon Gateway"
        config_server = uvicorn.Config(
            gateway_app, host=gw.host, port=gw.port,
            log_level=cfg.server.log_level,
        )
        gateway_uvicorn = uvicorn.Server(config_server)

        async def _run_gateway():
            logger.info("Gateway starting on %s:%d", gw.host, gw.port)
            await gateway_uvicorn.serve()

        _gateway_task = asyncio.create_task(_run_gateway())
        logger.info("Gateway standalone mode: %s:%d (%d platforms)",
                     gw.host, gw.port, registered)
    else:
        # Mount on main app
        app.mount("/gateway", gateway_server.app)
        logger.info("Gateway mounted on /gateway (%d platforms)", registered)

    # Connect adapters (e.g., Feishu WebSocket)
    for adapter in _gateway_adapters:
        if hasattr(adapter, 'connect'):
            try:
                await adapter.connect()
            except Exception as exc:
                logger.error("Adapter '%s' connect failed: %s",
                             getattr(adapter, 'platform_name', '?'), exc)

    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global router, dispatcher, guard, config

    config = DragonConfig.load()
    logger.info("Loading Dragon Agent...")

    # Router
    router = DragonRouter(
        model_path=config.router.model_path,
        n_threads=config.router.n_threads,
        n_ctx=config.router.n_ctx,
    )
    await router.initialize()
    logger.info("Router status: %s", router.status)

    # Dispatcher — all industries share global_api endpoint
    dispatcher = DragonDispatcher()
    ga = config.dispatch.global_api
    for industry, ic in config.dispatch.industries.items():
        dispatcher.register(industry, profile=ProviderProfile(
            name=industry,
            provider="sangyuye",
            model=ga.model,
            api_key_env=ga.api_key_env,
            base_url=ga.base_url,
            system_prompt=ic.system_prompt,
            timeout=ga.timeout_secs,
            max_retries=ga.max_retries,
        ))

    # Guard (use default values — config.guard fields may be outdated)
    guard = AntiLoopGuard()

    # ── Honest AI Pipeline ──────────────────────────────────────────
    jury = JuryDebate(dispatcher=dispatcher)
    web_searcher = WebSearcher()
    fact_checker = FactChecker(web_searcher=web_searcher, enable_web_search=True)
    consensus_builder = ConsensusBuilder(fact_checker=fact_checker)
    hall_tracker = HallucinationTracker()

    logger.info(
        "Honest pipeline ready: jury=%d jurors, factcheck=%s, consensus=%s",
        len(jury.juror_names), "enabled", "enabled",
    )

    # Skill Engine
    skill_engine = SkillEngine(
        skills_dir="dragon_data/skills",
        embedding_model=config.memory.embedding_model,
        auto_evolve=True,
    )

    # Tool Registry
    tool_registry = ToolRegistry()
    register_builtins(tool_registry)

    # Wire skill executor to dispatcher
    skill_engine.register_executor(
        lambda skill, ctx: dispatcher.dispatch_sync(
            industry="general",
            messages=[{"role": "user", "content": f"Execute skill: {skill.name}\\n\\nContext: {json.dumps(ctx)}\\n\\nInstructions:\\n{skill.content}"}],
        )
    ) if dispatcher else None

    # Gateway (Feishu / WeChat / Telegram / Discord)
    await _start_gateway(config)

    logger.info("Dragon Agent ready")
    yield

    # Shutdown
    logger.info("Shutting down...")
    # Disconnect adapters (e.g., Feishu WebSocket)
    for adapter in _gateway_adapters:
        if hasattr(adapter, 'disconnect'):
            try:
                await adapter.disconnect()
            except Exception as exc:
                logger.error("Adapter '%s' disconnect failed: %s",
                             getattr(adapter, 'platform_name', '?'), exc)
    if _gateway_task:
        _gateway_task.cancel()
        logger.info("Gateway task cancelled")
    if router:
        router.shutdown()


app = FastAPI(title="Dragon Agent", version="1.0.0", lifespan=lifespan)
from dragon.monitoring import router as monitoring_router
app.include_router(monitoring_router)


# ── Request/Response Models ──────────────────────────────

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    session_id: Optional[str] = None
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048

class ChatResponse(BaseModel):
    industry: str
    confidence: float
    difficulty: str
    model: str
    provider: str
    content: str
    usage: Optional[Dict[str, int]] = None
    latency_ms: int


class VoiceChatRequest(BaseModel):
    messages: List[Message]
    session_id: Optional[str] = None
    voice: str = "zh-CN-XiaoxiaoNeural"
    speed: float = 1.0


class HonestChatResponse(BaseModel):
    """Response from the honest AI pipeline with verification metadata."""
    answer: str                          # Final consensus answer (Markdown)
    confidence: float                    # Calibrated confidence 0-1
    agreement_level: str                 # "high" | "moderate" | "low" | "none"
    industry: str                        # Classified industry
    model_count: int                     # Number of models in jury
    winner: str = ""                     # Winning model
    verified_claims: int = 0             # Number of verified factual claims
    total_claims: int = 0                # Total claims extracted
    hallucination_risk: float = 0.0      # 0-1, lower is better
    sources: list = []                   # Source attributions
    disputed: list = []                  # Disputed claims
    minority_opinions: str = ""          # Dissenting views
    latency_ms: int = 0


# ── Routes ───────────────────────────────────────────────

@app.get("/health")
async def health():
    """Component health check."""
    return {
        "status": "healthy",
        "components": {
            "router": {
                "status": router.status.value if router else "unknown",
                "metrics": router.metrics.__dict__ if router else {},
            },
            "dispatcher": {
                "status": "ready" if dispatcher else "unknown",
            },
            "guard": {
                "status": "ready" if guard else "unknown",
            },
            "skills": skill_engine.stats() if skill_engine else {"status": "unknown"},
            "tools": tool_registry.stats() if tool_registry else {"status": "unknown"},
        },
        "version": "1.0.0",
    }


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Full conversation: classify → dispatch."""
    if not router or not dispatcher:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    start = time.monotonic()

    # Build query from last user message
    user_msg = next(
        (m.content for m in reversed(request.messages) if m.role == "user"),
        "",
    )

    # Step 1: Classify
    classification = await router.classify(user_msg)
    logger.info(
        "Classified: industry=%s confidence=%.2f difficulty=%s",
        classification.industry,
        classification.confidence,
        classification.difficulty,
    )

    # Step 2: Convert messages for dispatch
    dispatch_messages = [
        {"role": m.role, "content": m.content}
        for m in request.messages
    ]

    # Step 3: Dispatch
    result = await dispatcher.dispatch(
        industry=classification.industry,
        messages=dispatch_messages,
        stream=False,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )

    latency_ms = int((time.monotonic() - start) * 1000)

    # Step 4: Record in guard
    if guard:
        guard.record(
            action_type="MODEL_RESPONSE",
            action_name=f"dispatch:{classification.industry}",
            success=True,
            meta={"industry": classification.industry, "latency_ms": latency_ms},
        )

    return ChatResponse(
        industry=classification.industry,
        confidence=classification.confidence,
        difficulty=classification.difficulty,
        model=result.model,
        provider=result.provider,
        content=result.content,
        usage=result.usage,
        latency_ms=latency_ms,
    )


# ── Honest AI Chat ──────────────────────────────────────────

@app.post("/v1/chat/honest", response_model=HonestChatResponse)
async def chat_honest(request: ChatRequest):
    """
    Full honest AI pipeline:
    Router → Multi-model Dispatch → Jury Debate → Fact Check → Consensus.

    This is the core differentiator — every answer is debated by a jury
    of specialized models, fact-checked against web + knowledge base,
    and delivered with source attributions.
    """
    if not router or not dispatcher:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    if not jury or not fact_checker or not consensus_builder:
        raise HTTPException(status_code=503, detail="Honest pipeline not initialized")

    start = time.monotonic()

    # Build query from last user message
    user_msg = next(
        (m.content for m in reversed(request.messages) if m.role == "user"),
        "",
    )
    if not user_msg.strip():
        raise HTTPException(status_code=400, detail="Empty query")

    # Step 1: Classify industry
    classification = await router.classify(user_msg)
    industry = classification.industry
    logger.info(
        "Honest chat: industry=%s confidence=%.2f query=%.80s...",
        industry, classification.confidence, user_msg,
    )

    # Step 2: Dispatch to jury panel models (parallel)
    dispatch_messages = [
        {"role": m.role, "content": m.content}
        for m in request.messages
    ]
    juror_names = jury.juror_names

    async def _dispatch_one(name: str) -> tuple:
        """Dispatch to one juror's industry."""
        # Map juror name to industry (jury panel: (name, industry))
        juror_industry = None
        for jn, ji in jury._jury_panel:
            if jn == name:
                juror_industry = ji
                break
        ind = juror_industry or industry  # Fallback to classified industry

        result = await dispatcher.dispatch(
            industry=ind,
            messages=dispatch_messages,
            stream=False,
            temperature=request.temperature or 0.7,
            max_tokens=request.max_tokens or 2048,
        )
        return name, result.content, ind

    dispatch_tasks = [_dispatch_one(name) for name in juror_names]
    dispatch_results = await asyncio.gather(*dispatch_tasks)

    # Step 3: Build proposals for jury
    proposals = {}
    answers_by_model = {}
    for name, content, ind in dispatch_results:
        proposal_id = name.split("-")[0][:1].upper()  # e.g. "finance-juror" → "F"
        proposals[proposal_id] = {
            "summary": content[:2000],
            "author": name,
        }
        answers_by_model[name] = content

    logger.info("Dispatched to %d jurors, building proposals...", len(proposals))

    # Step 4: Jury deliberation
    verdict = await jury.deliberate(
        query=user_msg,
        proposals=proposals,
    )
    logger.info(
        "Jury verdict: winner=%s confidence=%.2f decision=%s",
        verdict.winner, verdict.confidence, verdict.decision.value,
    )

    # Step 5: Extract and verify factual claims from verdict
    claim_extractor = ClaimExtractor()
    all_claims = []
    for model_name, answer in answers_by_model.items():
        claims = claim_extractor.extract(answer, source_model=model_name)
        all_claims.extend(claims)

    fact_report = await fact_checker.verify_claims(
        claims=all_claims,
        question_context=user_msg,
    )

    # Step 6: Build consensus output
    consensus = await consensus_builder.build(
        verdict=verdict,
        question=user_msg,
        fact_check_report=fact_report,
    )

    # Step 7: Record hallucination metrics
    hall_report = hall_tracker.record(
        session_id=request.session_id or "default",
        consensus_result=consensus,
        fact_check_report=fact_report,
        verdict=verdict,
    )

    latency_ms = int((time.monotonic() - start) * 1000)

    # Format sources
    sources_out = []
    for src in getattr(consensus, "sources", []):
        if hasattr(src, "__dict__"):
            sources_out.append({
                "claim": getattr(src, "claim", "")[:200],
                "source_type": getattr(src, "source_type", "unknown"),
                "detail": getattr(src, "source_detail", "")[:200],
            })

    # Format disputed
    disputed_out = []
    for dc in getattr(consensus, "disputed_claims", []):
        disputed_out.append({
            "claim": getattr(dc, "claim", "")[:200],
            "positions": getattr(dc, "positions", {}),
        })

    logger.info(
        "Honest pipeline complete: claims=%d/%d verified, risk=%.2f, "
        "agreement=%s, latency=%dms",
        fact_report.verified_count if hasattr(fact_report, "verified_count") else 0,
        len(all_claims),
        hall_report.hallucination_rate,
        consensus.agreement_level.value if hasattr(consensus.agreement_level, "value") else str(consensus.agreement_level),
        latency_ms,
    )

    return HonestChatResponse(
        answer=consensus.answer,
        confidence=consensus.confidence,
        agreement_level=(
            consensus.agreement_level.value
            if hasattr(consensus.agreement_level, "value")
            else str(consensus.agreement_level)
        ),
        industry=industry,
        model_count=len(juror_names),
        winner=verdict.winner,
        verified_claims=(
            fact_report.verified_count
            if hasattr(fact_report, "verified_count")
            else 0
        ),
        total_claims=len(all_claims),
        hallucination_risk=hall_report.hallucination_rate,
        sources=sources_out,
        disputed=disputed_out,
        minority_opinions=getattr(verdict, "minority_report", "") or "",
        latency_ms=latency_ms,
    )


# ── Interrupt API ─────────────────────────────────────────────

interrupt_mgr: Optional[InterruptManager] = None


@app.on_event("startup")
async def startup_interrupt():
    global interrupt_mgr
    interrupt_mgr = get_interrupt_manager()


@app.post("/v1/interrupt/{session_id}")
async def interrupt_task(session_id: str, reason: str = "User requested interrupt"):
    """Interrupt a running task."""
    if not interrupt_mgr:
        raise HTTPException(status_code=503, detail="Interrupt manager not initialized")
    ok = interrupt_mgr.request_interrupt(session_id, reason)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No running task: {session_id}")
    return {"status": "interrupting", "session_id": session_id}


@app.get("/v1/tasks")
async def list_tasks():
    """List all running/completed tasks."""
    if not interrupt_mgr:
        return {"tasks": {}}
    tasks = interrupt_mgr.list_tasks()
    return {
        "tasks": {
            sid: {
                "state": s.state.value,
                "started_at": s.started_at,
                "progress": s.progress,
                "progress_pct": s.progress_pct,
                "error": s.error,
            }
            for sid, s in tasks.items()
        }
    }


@app.get("/v1/tasks/{session_id}")
async def get_task(session_id: str):
    """Get task status."""
    if not interrupt_mgr:
        raise HTTPException(status_code=503)
    status = interrupt_mgr.get_status(session_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Task not found: {session_id}")
    return {
        "session_id": status.session_id,
        "state": status.state.value,
        "started_at": status.started_at,
        "progress": status.progress,
        "progress_pct": status.progress_pct,
    }


# ── Streaming ───────────────────────────────────────────────
@app.post("/v1/chat/stream")
async def chat_stream(request: ChatRequest):
    """Streaming chat."""
    if not router or not dispatcher:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    user_msg = next(
        (m.content for m in reversed(request.messages) if m.role == "user"),
        "",
    )

    classification = await router.classify(user_msg)
    dispatch_messages = [
        {"role": m.role, "content": m.content}
        for m in request.messages
    ]

    import json

    async def generate():
        stream = dispatcher.dispatch_stream(
            industry=classification.industry,
            messages=dispatch_messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        yield f"data: {json.dumps({'industry': classification.industry, 'difficulty': classification.difficulty})}\n\n"
        async for chunk in stream:
            yield f"data: {json.dumps({'content': chunk.content, 'finish': chunk.finish_reason})}\n\n"
            if chunk.usage:
                yield f"data: {json.dumps({'usage': chunk.usage})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Industry": classification.industry},
    )


# ── Voice Chat ──────────────────────────────────────────────


@app.post("/v1/chat/voice")
async def chat_voice(request: VoiceChatRequest):
    """Voice streaming chat: SSE with text + base64 audio chunks."""
    if not router or not dispatcher:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    user_msg = next(
        (m.content for m in reversed(request.messages) if m.role == "user"),
        "",
    )

    classification = await router.classify(user_msg)
    dispatch_messages = [
        {"role": m.role, "content": m.content}
        for m in request.messages
    ]

    from dragon.voice_engine import VoiceEngine
    import base64

    async def generate():
        engine = VoiceEngine(voice=request.voice, speed=request.speed)
        await engine.start()

        # Start LLM stream
        stream = dispatcher.dispatch_stream(
            industry=classification.industry,
            messages=dispatch_messages,
            temperature=0.7,
            max_tokens=2048,
        )

        # Send metadata
        yield f"data: {json.dumps({'type': 'meta', 'industry': classification.industry, 'voice': request.voice})}\n\n"

        text_buffer = ""

        async for chunk in stream:
            if not chunk.content:
                continue

            text_buffer += chunk.content

            # Feed to voice engine
            engine.consume(chunk.content)

            # Try to get ready audio
            try:
                audio_item = engine.audio_queue.get_nowait()
                if audio_item:
                    sentence_text, audio_bytes = audio_item
                    audio_b64 = base64.b64encode(audio_bytes).decode()
                    yield f"data: {json.dumps({'type': 'audio', 'text': sentence_text, 'audio_base64': audio_b64})}\n\n"
            except asyncio.QueueEmpty:
                pass

            # Yield text chunk
            yield f"data: {json.dumps({'type': 'text', 'content': chunk.content})}\n\n"

            if chunk.finish_reason:
                break

        # Flush remaining
        await engine.flush()

        # Drain remaining audio
        while True:
            item = await engine.next_audio()
            if item is None:
                break
            sentence_text, audio_bytes = item
            audio_b64 = base64.b64encode(audio_bytes).decode()
            yield f"data: {json.dumps({'type': 'audio', 'text': sentence_text, 'audio_base64': audio_b64})}\n\n"

        await engine.stop()
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"X-Industry": classification.industry},
    )


# ── Backup API ───────────────────────────────────────────────

backup_instance: "Optional[DragonBackup]" = None


@app.on_event("startup")
async def startup_backup():
    global backup_instance
    try:
        from dragon.backup import DragonBackup
        if config and config.backup.endpoint:
            backup_instance = DragonBackup.from_config(config)
            logger.info("Backup module initialized")
    except Exception as e:
        logger.warning("Backup init skipped: %s", e)


@app.post("/v1/backup")
async def trigger_backup():
    """Manually trigger a backup."""
    if not backup_instance:
        raise HTTPException(status_code=503, detail="Backup not configured")
    try:
        manifest = backup_instance.backup()
        return {"status": "ok", "manifest": manifest.to_dict()}
    except BackupLockError:
        raise HTTPException(status_code=409, detail="Another backup is in progress")
    except BackupUploadError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/v1/restore")
async def trigger_restore(backup_id: Optional[str] = None):
    """Restore from cloud backup."""
    if not backup_instance:
        raise HTTPException(status_code=503, detail="Backup not configured")
    try:
        manifest = backup_instance.restore(backup_id)
        return {"status": "ok", "manifest": manifest.to_dict(),
                "warning": "Data restored to dragon_data/restored/ — manually activate to use"}
    except BackupNotFoundError:
        raise HTTPException(status_code=404, detail="No backup found")
    except BackupRestoreError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/v1/backups")
async def list_backups():
    """List available backups."""
    if not backup_instance:
        raise HTTPException(status_code=503, detail="Backup not configured")
    backups = backup_instance.list_backups()
    return {"backups": [b.to_dict() for b in backups]}


@app.delete("/v1/backups/{backup_id}")
async def delete_backup(backup_id: str):
    """Delete a specific backup."""
    if not backup_instance:
        raise HTTPException(status_code=503, detail="Backup not configured")
    ok = backup_instance.delete_backup(backup_id)
    return {"status": "deleted" if ok else "not_found", "backup_id": backup_id}


# ── Expert Consultation API ────────────────────────────────────

consult_engine: "Optional[ExpertConsultation]" = None


@app.on_event("startup")
async def startup_consult():
    global consult_engine
    try:
        from dragon.consult import ExpertConsultation
        if dispatcher:
            consult_engine = ExpertConsultation(
                dispatcher=dispatcher,
                jury=None,  # lazy init if needed
                cost_optimizer=None,
            )
            logger.info("Consultation engine initialized")
    except Exception as e:
        logger.warning("Consultation init skipped: %s", e)


@app.get("/v1/consult/assess")
async def assess_difficulty(q: str, session_id: str = "default"):
    """Assess problem difficulty and consultation need."""
    if not router or not consult_engine:
        raise HTTPException(status_code=503, detail="Not initialized")
    
    classification = await router.classify(q)
    assessment = consult_engine.assess(
        q, classification.difficulty_score, classification.industry
    )
    approval_msg = consult_engine.request_approval(assessment)
    
    return {
        "industry": classification.industry,
        "difficulty": classification.difficulty,
        "difficulty_score": classification.difficulty_score,
        "assessment": assessment.to_dict(),
        "approval_message": approval_msg,
    }


@app.post("/v1/consult")
async def run_consultation(request: dict):
    """Run expert consultation (requires user approval)."""
    if not router or not consult_engine:
        raise HTTPException(status_code=503, detail="Not initialized")
    
    query = request.get("query", "")
    allow = request.get("allow_consult", False)
    
    if not allow:
        classification = await router.classify(query)
        assessment = consult_engine.assess(
            query, classification.difficulty_score, classification.industry
        )
        return {
            "status": "approval_required",
            "assessment": assessment.to_dict(),
            "message": assessment.warning_message,
        }
    
    # Approved — run full consultation
    classification = await router.classify(query)
    try:
        result = await consult_engine.consult(
            query, classification.industry
        )
        return {
            "status": "completed",
            "solved": result.solved,
            "solution": result.solution,
            "confidence": result.confidence,
            "panel_used": result.panel_used,
            "debate_rounds": result.debate_rounds,
            "cost_usd": result.cost_usd,
            "minority_opinions": result.minority_opinions,
            "cannot_solve_reason": result.cannot_solve_reason,
        }
    except Exception as e:
        logger.exception("Consultation failed")
        return {
            "status": "failed",
            "solved": False,
            "cannot_solve_reason": f"专家会诊异常: {e}",
        }


# ── Skill API ────────────────────────────────────────────────────

@app.get("/v1/skills")
async def list_skills(category: str = None):
    """List all registered skills."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    skills = skill_engine.list_skills()
    if category:
        skills = [s for s in skills if category in s.get("tags", [])]
    return {"skills": skills, "total": len(skills)}


@app.get("/v1/skills/search")
async def search_skills(q: str, top_k: int = 5):
    """Semantic search for skills matching a query."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    matches = await skill_engine.discover(q, top_k=top_k)
    return {
        "query": q,
        "matches": [
            {
                "name": m.skill_name,
                "similarity": round(m.similarity, 3),
                "description": m.skill.meta.description,
                "version": m.skill.meta.version,
                "success_rate": round(m.skill.success_rate, 3),
            }
            for m in matches
        ],
    }


@app.post("/v1/skills")
async def create_skill_endpoint(
    name: str,
    description: str,
    content: str,
    tags: List[str] = None,
    version: str = "1.0.0",
):
    """Register a new skill."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    skill = skill_engine.register(
        name=name, description=description, content=content,
        tags=tags or [], version=version,
    )
    return {"status": "created", "name": skill.name, "version": skill.meta.version}


@app.get("/v1/skills/{name}")
async def get_skill_endpoint(name: str):
    """Get skill details including version history."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    skill = skill_engine.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {
        "name": skill.name, "description": skill.meta.description,
        "version": skill.meta.version, "tags": skill.meta.tags,
        "status": skill.meta.status, "content": skill.content[:2000],
        "versions": skill.get_version_history(),
    }


@app.delete("/v1/skills/{name}")
async def delete_skill_endpoint(name: str):
    """Delete a skill."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    if not skill_engine.delete(name):
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return {"status": "deleted", "name": name}


@app.post("/v1/skills/{name}/evolve")
async def evolve_skill_endpoint(name: str, new_content: str, reason: str = ""):
    """Evolve a skill to a new version."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    skill = skill_engine.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    new_ver = skill.evolve(new_content, reason or "Manual evolution")
    return {"status": "evolved", "name": name, "new_version": new_ver}


@app.post("/v1/skills/{name}/rollback")
async def rollback_skill_endpoint(name: str):
    """Rollback a skill to its previous version."""
    if not skill_engine:
        raise HTTPException(status_code=503, detail="Skill engine not initialized")
    skill = skill_engine.get(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    result = skill.rollback()
    if result is None:
        return {"status": "no_rollback_needed", "name": name}
    return {"status": "rolled_back", "name": name, "version": result}


# ── Tool API ─────────────────────────────────────────────────────

@app.get("/v1/gateway/status")
async def gateway_status():
    """Check gateway status and connected platforms."""
    if not gateway_server:
        return {
            "enabled": False,
            "message": "Gateway not configured. Add 'gateway' section to config.yaml.",
        }
    return {
        "enabled": True,
        "standalone": config.gateway.standalone if config else True,
        "port": config.gateway.port if config else 8781,
        "platforms": {
            name: {
                "webhook": adapter.webhook_path,
                "connected": getattr(adapter, '_connected', False),
                "mode": getattr(adapter, 'connection_mode', 'unknown'),
            }
            for name, adapter in gateway_server.adapters.items()
        },
    }


# ── Pairing API ───────────────────────────────────────────────────

@app.get("/v1/pairing/list")
async def pairing_list(platform: str = None):
    """List pending pairing requests."""
    if not gateway_server or not hasattr(gateway_server.processor, 'pairing_store'):
        raise HTTPException(status_code=503, detail="Gateway not available")
    ps = gateway_server.processor.pairing_store
    if ps is None:
        return {"pending": []}
    return {"pending": ps.list_pending(platform)}


@app.get("/v1/pairing/approved")
async def pairing_approved(platform: str = None):
    """List approved users."""
    if not gateway_server or not hasattr(gateway_server.processor, 'pairing_store'):
        raise HTTPException(status_code=503, detail="Gateway not available")
    ps = gateway_server.processor.pairing_store
    if ps is None:
        return {"approved": []}
    return {"approved": ps.list_approved(platform)}


@app.post("/v1/pairing/approve")
async def pairing_approve(platform: str, code: str):
    """Approve a pairing code."""
    if not gateway_server or not hasattr(gateway_server.processor, 'pairing_store'):
        raise HTTPException(status_code=503, detail="Gateway not available")
    ps = gateway_server.processor.pairing_store
    if ps is None:
        raise HTTPException(status_code=503, detail="Pairing not available")
    result = ps.approve_code(platform, code)
    if result is None:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    return {"status": "approved", **result}


@app.post("/v1/pairing/revoke")
async def pairing_revoke(platform: str, user_id: str):
    """Revoke an approved user."""
    if not gateway_server or not hasattr(gateway_server.processor, 'pairing_store'):
        raise HTTPException(status_code=503, detail="Gateway not available")
    ps = gateway_server.processor.pairing_store
    if ps is None:
        raise HTTPException(status_code=503, detail="Pairing not available")
    ok = ps.revoke(platform, user_id)
    return {"status": "revoked" if ok else "not_found"}


@app.get("/v1/tools")
async def list_tools_endpoint(category: str = None):
    """List all registered tools."""
    if not tool_registry:
        raise HTTPException(status_code=503, detail="Tool registry not initialized")
    return {"tools": tool_registry.list_tools(category=category)}


@app.get("/v1/tools/search")
async def search_tools_endpoint(q: str):
    """Search for tools by name, description, or tags."""
    if not tool_registry:
        raise HTTPException(status_code=503, detail="Tool registry not initialized")
    return {"query": q, "results": tool_registry.search(q)}


@app.post("/v1/tools/{name}/call")
async def call_tool_endpoint(name: str, args: dict):
    """Execute a tool with the given arguments."""
    if not tool_registry:
        raise HTTPException(status_code=503, detail="Tool registry not initialized")
    result = await tool_registry.call(name, args)
    return result.to_dict()
