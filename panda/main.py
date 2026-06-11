"""
Panda Agent API Server.

FastAPI entry point with:
- /v1/chat — full conversation with routing + dispatch
- /v1/chat/stream — SSE streaming
- /health — component health checks
"""

import time
import logging
import json
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from panda.config import PandaConfig
from panda.router import PandaRouter, RouteResult, RouterStatus as _ignore_RouterStatus
from panda.dispatch import PandaDispatcher, ProviderProfile, DispatchResult as _ignore_DispatchResult
from panda.guard import AntiLoopGuard, LoopAction as _ignore_LoopAction
from panda.interrupt import get_interrupt_manager, TaskInterrupted, InterruptManager
from panda.backup import PandaBackup, BackupLockError, BackupUploadError, BackupRestoreError, BackupNotFoundError
from panda.consult import ExpertConsultation, ConsultationAssessment, ConsultationResult
from panda.skill import SkillEngine
from panda.tool import ToolRegistry
from panda.tool.builtins import register_builtins

logger = logging.getLogger("panda.api")

# ── Globals ──────────────────────────────────────────────
router: Optional[PandaRouter] = None
dispatcher: Optional[PandaDispatcher] = None
guard: Optional[AntiLoopGuard] = None
config: Optional[PandaConfig] = None
skill_engine: Optional[SkillEngine] = None
tool_registry: Optional[ToolRegistry] = None


# ── Lifespan ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global router, dispatcher, guard, config

    config = PandaConfig.load()
    logger.info("Loading Panda Agent...")

    # Router
    router = PandaRouter(
        model_path=config.router.model_path,
        n_threads=config.router.n_threads,
        n_ctx=config.router.n_ctx,
        temperature=config.router.temperature,
        max_tokens=config.router.max_tokens,
    )
    router.initialize()
    logger.info("Router status: %s", router.status)

    # Dispatcher — all industries share global_api endpoint
    dispatcher = PandaDispatcher()
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

    # Guard
    guard = AntiLoopGuard(
        max_consecutive_repeats=config.guard.max_consecutive_repeats,
        max_loop_rounds=config.guard.max_loop_rounds,
        max_ineffective_retries=config.guard.max_ineffective_retries,
        window_size=config.guard.window_size,
    )

    # Skill Engine
    skill_engine = SkillEngine(
        skills_dir="panda_data/skills",
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
            messages=[{"role": "user", "content": f"Execute skill: {skill.name}\n\nContext: {json.dumps(ctx)}\n\nInstructions:\n{skill.content}"}],
        )
    ) if dispatcher else None

    logger.info("Panda Agent ready")
    yield

    # Shutdown
    logger.info("Shutting down...")
    if router:
        router.shutdown()


app = FastAPI(title="Panda Agent", version="1.0.0", lifespan=lifespan)


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


# ── Backup API ───────────────────────────────────────────────

backup_instance: "Optional[PandaBackup]" = None


@app.on_event("startup")
async def startup_backup():
    global backup_instance
    try:
        from panda.backup import PandaBackup
        if config and config.backup.endpoint:
            backup_instance = PandaBackup.from_config(config)
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
                "warning": "Data restored to panda_data/restored/ — manually activate to use"}
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
        from panda.consult import ExpertConsultation
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
