"""Agent endpoints backed by the in-process Copilot SDK runtime."""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from opentelemetry import context as otel_context
from opentelemetry import trace
from sse_starlette.sse import EventSourceResponse

from app.constants import PRIMARY_USE_CASE
from app.models import (
    AgentCancelRequest,
    AgentRequest,
    DoneEvent,
    ErrorEvent,
    FollowUpQuestionsEvent,
    Message,
    MessageRole,
    UserInputResponseRequest,
)
from app.services.follow_up_service import generate_follow_ups

logger = logging.getLogger(__name__)

router = APIRouter()

# Runs are detached from the HTTP stream so a browser disconnect does not lose
# the assistant response or its persistence record.
_background_runs: set[asyncio.Task[None]] = set()


@router.post("/chat")
async def chat(body: AgentRequest, request: Request) -> EventSourceResponse:
    """Run the ARE in-process and stream typed events as SSE."""
    cosmos = request.app.state.cosmos_service
    copilot_agent = request.app.state.copilot_agent

    eval_run_id = request.headers.get("x-kratos-eval-run-id") or ""
    request.state.eval_run_id = eval_run_id
    span = trace.get_current_span()
    span.set_attribute("kratos.use_case", PRIMARY_USE_CASE)
    span.set_attribute("kratos.conversation_id", body.conversationId)
    if eval_run_id:
        span.set_attribute("kratos.eval_run_id", eval_run_id)

    app = request.app
    otel_ctx = otel_context.get_current()
    event_queue: asyncio.Queue[dict[str, str] | object] = asyncio.Queue()
    sentinel = object()

    async def run_agent() -> None:
        token = otel_context.attach(otel_ctx)
        start_time = time.monotonic()
        assistant_content_parts: list[str] = []
        collected_thoughts: list[str] = []
        collected_tool_calls: list[dict] = []
        run_stats = {
            "promptTokens": 0,
            "completionTokens": 0,
            "reasoningTokens": 0,
            "totalTokens": 0,
            "timeToFirstTokenMs": 0,
            "modelLatencyMs": 0,
        }
        total_tool_calls = 0

        try:
            await cosmos.upsert_message(
                Message(
                    id=str(uuid.uuid4()),
                    conversationId=body.conversationId,
                    role=MessageRole.USER,
                    content=body.message,
                    createdAt=datetime.now(UTC),
                )
            )

            copilot_agent.set_conversation_use_case(body.conversationId, PRIMARY_USE_CASE)
            copilot_agent.set_conversation_mcp_tokens(body.conversationId, body.mcpAccessTokens)
            attachments = [attachment.model_dump() for attachment in body.attachments]

            async for event in copilot_agent.run(
                message=body.message,
                conversation_id=body.conversationId,
                attachments=attachments,
                use_case=PRIMARY_USE_CASE,
                eval_run_id=eval_run_id or None,
            ):
                event_data = event.model_dump()
                event_name = event.type

                if event_name == "content":
                    assistant_content_parts.append(event.content)
                elif event_name == "thought":
                    collected_thoughts.append(event.content)
                elif event_name == "tool_call":
                    collected_tool_calls.append(event_data)
                    if event.status in {"completed", "failed"}:
                        total_tool_calls += 1
                elif event_name == "usage":
                    for key in run_stats:
                        if key in event_data:
                            run_stats[key] = int(event_data[key])

                await event_queue.put({"event": event_name, "data": json.dumps(event_data)})

            agent_stats = copilot_agent.get_run_stats(body.conversationId)
            run_stats.update(
                {
                    "promptTokens": agent_stats["prompt_tokens"],
                    "completionTokens": agent_stats["completion_tokens"],
                    "reasoningTokens": agent_stats["reasoning_tokens"],
                    "totalTokens": agent_stats["total_tokens"],
                    "timeToFirstTokenMs": agent_stats["time_to_first_token_ms"],
                    "modelLatencyMs": agent_stats["model_latency_ms"],
                }
            )
            full_response = "".join(assistant_content_parts)
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            run_stats["totalDurationMs"] = elapsed_ms
            run_stats["totalToolCalls"] = total_tool_calls
            await cosmos.upsert_message(
                Message(
                    id=str(uuid.uuid4()),
                    conversationId=body.conversationId,
                    role=MessageRole.ASSISTANT,
                    content=full_response,
                    metadata={
                        "thoughts": collected_thoughts,
                        "toolCalls": collected_tool_calls,
                        "runStats": run_stats,
                    },
                    createdAt=datetime.now(UTC),
                )
            )

            try:
                registry = getattr(app.state, "registries", {}).get(PRIMARY_USE_CASE)
                skills = registry.get_enabled_skills() if registry else []
                follow_ups = await generate_follow_ups(
                    body.message,
                    full_response,
                    [skill.name for skill in skills],
                )
                if follow_ups:
                    follow_up_event = FollowUpQuestionsEvent(questions=follow_ups)
                    await event_queue.put(
                        {
                            "event": follow_up_event.type,
                            "data": json.dumps(follow_up_event.model_dump()),
                        }
                    )
            except Exception:
                logger.debug("Follow-up generation skipped", exc_info=True)

            done = DoneEvent(
                conversationId=body.conversationId,
                totalDurationMs=elapsed_ms,
                totalToolCalls=total_tool_calls,
                promptTokens=run_stats["promptTokens"],
                completionTokens=run_stats["completionTokens"],
                reasoningTokens=run_stats["reasoningTokens"],
                totalTokens=run_stats["totalTokens"],
                timeToFirstTokenMs=run_stats["timeToFirstTokenMs"],
                modelLatencyMs=run_stats["modelLatencyMs"],
            )
            await event_queue.put({"event": done.type, "data": json.dumps(done.model_dump())})
        except Exception:
            logger.exception("In-process agent failed for conversation=%s", body.conversationId)
            error = ErrorEvent(message="An internal error occurred", code="AGENT_ERROR")
            await event_queue.put({"event": error.type, "data": json.dumps(error.model_dump())})
        finally:
            await event_queue.put(sentinel)
            otel_context.detach(token)

    run_task = asyncio.create_task(run_agent())
    _background_runs.add(run_task)
    run_task.add_done_callback(_background_runs.discard)

    async def event_generator():  # noqa: ANN202
        while True:
            item = await event_queue.get()
            if item is sentinel:
                break
            yield item

    return EventSourceResponse(event_generator())


@router.post("/user-input")
async def user_input_response(body: UserInputResponseRequest, request: Request) -> JSONResponse:
    """Resolve an approval or other user-input request from the active turn."""
    copilot_agent = request.app.state.copilot_agent
    if not copilot_agent.resolve_user_input(body.requestId, body.answer, body.conversationId):
        raise HTTPException(status_code=404, detail="User input request is not active")
    return JSONResponse(content={"accepted": True})


@router.post("/cancel")
async def cancel_agent(body: AgentCancelRequest, request: Request) -> JSONResponse:
    """Cancel the active agent turn for a conversation."""
    copilot_agent = request.app.state.copilot_agent
    if not copilot_agent.cancel(body.conversationId):
        raise HTTPException(status_code=404, detail="Conversation has no active agent session")
    return JSONResponse(content={"cancelled": True})
