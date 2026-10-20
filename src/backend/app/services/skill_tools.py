"""Custom Copilot tools retained by the Agent Reliability Engineer."""

from contextvars import ContextVar

from copilot.tools import define_tool
from opentelemetry import trace

from app.config import Settings
from app.services.app_insights_investigation import (
    AppInsightsInvestigationParams,
    AppInsightsInvestigationService,
)

tracer = trace.get_tracer(__name__)

_ctx_use_case: ContextVar[str] = ContextVar("kratos_use_case", default="")
_ctx_conversation_id: ContextVar[str] = ContextVar("kratos_conversation_id", default="")
_ctx_eval_run_id: ContextVar[str] = ContextVar("kratos_eval_run_id", default="")


def _set_kratos_attrs(span: trace.Span) -> None:
    """Stamp active ARE request context onto a tool span."""
    use_case = _ctx_use_case.get()
    if use_case:
        span.set_attribute("kratos.use_case", use_case)
    conversation_id = _ctx_conversation_id.get()
    if conversation_id:
        span.set_attribute("kratos.conversation_id", conversation_id)
    eval_run_id = _ctx_eval_run_id.get()
    if eval_run_id:
        span.set_attribute("kratos.eval_run_id", eval_run_id)


@define_tool(
    description=(
        "Investigate a suspected reliability cause using bounded, read-only queries "
        "against the configured Application Insights resource"
    )
)
async def app_insights_investigation(params: AppInsightsInvestigationParams) -> dict:
    with tracer.start_as_current_span("skill.app_insights_investigation") as span:
        _set_kratos_attrs(span)
        span.set_attribute("are.service_name", params.service_name)
        span.set_attribute("are.lookback_hours", params.lookback_hours)
        service = AppInsightsInvestigationService(Settings())
        try:
            return await service.investigate(params)
        finally:
            await service.close()


ALL_TOOLS = [app_insights_investigation]
TOOL_MAP = {tool.name: tool for tool in ALL_TOOLS}
