"""Bounded, read-only Application Insights evidence retrieval for the ARE."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from azure.identity import DefaultAzureCredential
from azure.monitor.query import LogsQueryClient, LogsQueryStatus
from pydantic import BaseModel, Field

from app.config import Settings

logger = logging.getLogger(__name__)

_MAX_LOOKBACK_HOURS = 24
_MAX_ROWS_PER_QUERY = 50
_QUERY_TABLES = ("requests", "dependencies", "exceptions", "traces", "customEvents")


class AppInsightsInvestigationParams(BaseModel):
    """Inputs accepted by the read-only Application Insights investigation tool."""

    service_name: str = Field(
        min_length=1,
        max_length=120,
        description="Service or cloud role name to investigate.",
    )
    suspected_cause: str = Field(
        min_length=1,
        max_length=500,
        description="The suspected cause to test against telemetry evidence.",
    )
    lookback_hours: int = Field(
        default=4,
        ge=1,
        le=_MAX_LOOKBACK_HOURS,
        description="Whole hours of telemetry to inspect, from 1 through 24.",
    )
    max_rows_per_query: int = Field(
        default=20,
        ge=1,
        le=_MAX_ROWS_PER_QUERY,
        description="Maximum evidence rows returned by each fixed query, from 1 through 50.",
    )


@dataclass(frozen=True)
class InvestigationQuery:
    query_id: str
    table: str
    purpose: str
    kql: str


class AppInsightsInvestigationService:
    """Runs fixed evidence queries against one configured App Insights target."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._resource_id = settings.application_insights_resource_id.strip()
        self._workspace_id = settings.application_insights_workspace_id.strip()
        self._credential = None
        if client is None:
            self._credential = DefaultAzureCredential()
            client = LogsQueryClient(self._credential)
        self._client = client

    async def investigate(self, params: AppInsightsInvestigationParams) -> dict[str, Any]:
        """Return bounded evidence and provenance; never accepts arbitrary KQL or resource IDs."""
        target = self._resource_id or self._workspace_id
        if not target:
            return {
                "status": "error",
                "error": "Application Insights is not configured",
                "requiredConfiguration": [
                    "APPLICATION_INSIGHTS_RESOURCE_ID",
                    "APPLICATION_INSIGHTS_WORKSPACE_ID",
                ],
            }

        queries = build_investigation_queries(params)
        results = await asyncio.gather(
            *(asyncio.to_thread(self._run_query, target, query.kql, params.lookback_hours) for query in queries),
            return_exceptions=True,
        )

        evidence: list[dict[str, Any]] = []
        query_receipts: list[dict[str, Any]] = []
        failed_queries: list[dict[str, str]] = []
        partial = False

        for query, result in zip(queries, results, strict=True):
            receipt = {
                "queryId": query.query_id,
                "table": query.table,
                "purpose": query.purpose,
                "lookbackHours": params.lookback_hours,
                "maxRows": params.max_rows_per_query,
                "kql": query.kql,
            }
            if isinstance(result, BaseException):
                message = str(result)
                failed_queries.append({"queryId": query.query_id, "error": message})
                query_receipts.append({**receipt, "status": "failed", "rowCount": 0})
                logger.warning("Application Insights query %s failed: %s", query.query_id, message)
                continue

            rows, is_partial, partial_error = _extract_rows(result)
            partial = partial or is_partial
            for row in rows[: params.max_rows_per_query]:
                evidence.append(
                    {
                        "evidenceId": f"{query.query_id}-R{len(evidence) + 1}",
                        "queryId": query.query_id,
                        "table": query.table,
                        "data": row,
                    }
                )
            query_receipts.append(
                {
                    **receipt,
                    "status": "partial" if is_partial else "succeeded",
                    "rowCount": min(len(rows), params.max_rows_per_query),
                    **({"partialError": partial_error} if partial_error else {}),
                }
            )

        if failed_queries and not evidence:
            status = "error"
        elif failed_queries or partial:
            status = "partial"
        else:
            status = "success"

        return {
            "status": status,
            "target": "configured-application-insights-resource",
            "readOnly": True,
            "serviceName": params.service_name,
            "suspectedCause": params.suspected_cause,
            "lookbackHours": params.lookback_hours,
            "evidence": evidence,
            "queries": query_receipts,
            "failedQueries": failed_queries,
            "guidance": (
                "Treat evidence rows as observations, not conclusions. Cite evidenceId and queryId, "
                "and report contradictory evidence, unknowns, and failed or partial queries."
            ),
        }

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)
        if self._credential is not None:
            await asyncio.to_thread(self._credential.close)

    def _run_query(self, target: str, kql: str, lookback_hours: int) -> Any:
        timespan = timedelta(hours=lookback_hours)
        if target.startswith("/"):
            try:
                return self._client.query_resource(resource_id=target, query=kql, timespan=timespan)
            except TypeError:
                return self._client.query_resource(resource_uri=target, query=kql, timespan=timespan)
        return self._client.query_workspace(workspace_id=target, query=kql, timespan=timespan)


def build_investigation_queries(params: AppInsightsInvestigationParams) -> list[InvestigationQuery]:
    """Build the fixed query set for a suspected-cause investigation."""
    service = _escape_kql_string(params.service_name.strip())
    cause_terms = _cause_terms(params.suspected_cause)
    cause_filter = " or ".join(f"search_text contains '{_escape_kql_string(term)}'" for term in cause_terms)
    common_filter = f"| where isempty('{service}') or cloud_RoleName =~ '{service}'"
    take = params.max_rows_per_query

    return [
        InvestigationQuery(
            query_id="AI-REQUESTS",
            table="requests",
            purpose="Recent failed or slow requests for the selected service",
            kql=f"""requests
| where timestamp > ago({params.lookback_hours}h)
{common_filter}
| where success == false or duration > 2s
| project timestamp, operation_Id, name, success, resultCode, duration, cloud_RoleName, url
| order by timestamp desc
| take {take}""",
        ),
        InvestigationQuery(
            query_id="AI-DEPENDENCIES",
            table="dependencies",
            purpose="Recent failed or slow downstream dependencies for the selected service",
            kql=f"""dependencies
| where timestamp > ago({params.lookback_hours}h)
{common_filter}
| where success == false or duration > 2s
| project timestamp, operation_Id, name, target, type, success, resultCode, duration, cloud_RoleName
| order by timestamp desc
| take {take}""",
        ),
        InvestigationQuery(
            query_id="AI-EXCEPTIONS",
            table="exceptions",
            purpose="Recent exceptions for the selected service",
            kql=f"""exceptions
| where timestamp > ago({params.lookback_hours}h)
{common_filter}
| project timestamp, operation_Id, type, outerMessage, problemId, severityLevel, cloud_RoleName
| order by timestamp desc
| take {take}""",
        ),
        InvestigationQuery(
            query_id="AI-TRACES",
            table="traces",
            purpose="Trace messages containing terms from the suspected cause",
            kql=f"""traces
| where timestamp > ago({params.lookback_hours}h)
{common_filter}
| extend search_text = tolower(strcat(message, ' ', tostring(customDimensions)))
| where {cause_filter}
| project timestamp, operation_Id, message, severityLevel, cloud_RoleName, customDimensions
| order by timestamp desc
| take {take}""",
        ),
        InvestigationQuery(
            query_id="AI-CHANGES",
            table="customEvents",
            purpose="Deployment or configuration events containing terms from the suspected cause",
            kql=f"""customEvents
| where timestamp > ago({params.lookback_hours}h)
{common_filter}
| extend search_text = tolower(strcat(name, ' ', tostring(customDimensions)))
| where name has_any ('deploy', 'deployment', 'release', 'config', 'configuration', 'change')
    or {cause_filter}
| project timestamp, operation_Id, name, cloud_RoleName, customDimensions
| order by timestamp desc
| take {take}""",
        ),
    ]


def _cause_terms(suspected_cause: str) -> list[str]:
    terms = re.findall(r"[a-z0-9][a-z0-9_.:/-]*", suspected_cause.lower())
    meaningful = [term for term in terms if len(term) >= 3]
    return list(dict.fromkeys(meaningful))[:8] or ["unknown"]


def _escape_kql_string(value: str) -> str:
    return value.replace("'", "''")


def _extract_rows(response: Any) -> tuple[list[dict[str, Any]], bool, str]:
    status = getattr(response, "status", None)
    if status == LogsQueryStatus.SUCCESS:
        tables = getattr(response, "tables", None) or []
        return _table_rows(tables), False, ""

    partial_data = getattr(response, "partial_data", None) or []
    if partial_data:
        error = getattr(response, "partial_error", None)
        return _table_rows(partial_data), True, str(error or "")

    error = getattr(response, "partial_error", None)
    return [], status != LogsQueryStatus.SUCCESS, str(error or "")


def _table_rows(tables: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in tables:
        columns = [getattr(column, "name", str(column)) for column in (getattr(table, "columns", None) or [])]
        for row in getattr(table, "rows", None) or []:
            rows.append(dict(zip(columns, row, strict=False)))
    return rows
