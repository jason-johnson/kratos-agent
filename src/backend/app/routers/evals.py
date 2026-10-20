"""ARE evaluation scenario CRUD and run management.

Endpoints:
    GET    /api/evals/scenarios
    POST   /api/evals/scenarios/generate
    PUT    /api/evals/scenarios/{name}
    DELETE /api/evals/scenarios/{name}
    POST   /api/evals/run
    GET    /api/evals/runs
    GET    /api/evals/runs/{run_id}
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_authenticated_user
from app.constants import PRIMARY_USE_CASE
from app.models import (
    EvalRun,
    EvalRunList,
    EvalRunRequest,
    EvalScenario,
    EvalScenarioList,
    GenerateScenariosRequest,
    GenerateScenariosResponse,
)
from app.services.eval_service import EvalService
from app.services.eval_storage import EvalStorage

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_authenticated_user)])


def _get_storage(request: Request) -> EvalStorage:
    storage: EvalStorage | None = getattr(request.app.state, "eval_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Eval storage is not initialized")
    return storage


def _get_service(request: Request) -> EvalService:
    svc: EvalService | None = getattr(request.app.state, "eval_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Eval service is not initialized")
    return svc


# ── Scenarios ────────────────────────────────────────────────────────────────


@router.get("/scenarios", response_model=EvalScenarioList)
async def list_scenarios(request: Request) -> EvalScenarioList:
    storage = _get_storage(request)
    scenarios = await storage.list_scenarios(PRIMARY_USE_CASE)
    return EvalScenarioList(scenarios=scenarios)


@router.post("/scenarios/generate", response_model=GenerateScenariosResponse)
async def generate_scenarios(
    body: GenerateScenariosRequest,
    request: Request,
) -> GenerateScenariosResponse:
    storage = _get_storage(request)
    service = _get_service(request)
    try:
        scenarios = await service.generate_scenarios(
            use_case=PRIMARY_USE_CASE,
            count=body.count,
            instructions=body.instructions,
        )
    except Exception as exc:
        logger.exception("ARE scenario generation failed")
        raise HTTPException(status_code=502, detail=f"Scenario generation failed: {exc}") from exc

    if body.persist:
        for s in scenarios:
            await storage.upsert_scenario(PRIMARY_USE_CASE, s)
    return GenerateScenariosResponse(scenarios=scenarios, persisted=body.persist)


@router.put("/scenarios/{name}", response_model=EvalScenario)
async def upsert_scenario(
    name: str,
    scenario: EvalScenario,
    request: Request,
) -> EvalScenario:
    # Allow renaming if the path name doesn't match — prefer body name
    # but reject obvious mismatches that suggest a client bug.
    if scenario.name != name and not scenario.name:
        scenario = scenario.model_copy(update={"name": name})
    storage = _get_storage(request)
    await storage.upsert_scenario(PRIMARY_USE_CASE, scenario)
    return scenario


@router.delete("/scenarios/{name}")
async def delete_scenario(name: str, request: Request) -> dict[str, bool]:
    storage = _get_storage(request)
    removed = await storage.delete_scenario(PRIMARY_USE_CASE, name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Scenario '{name}' not found")
    return {"deleted": True}


# ── Runs ─────────────────────────────────────────────────────────────────────


@router.post("/run", response_model=EvalRun)
async def start_run(
    body: EvalRunRequest,
    request: Request,
    user: dict = Depends(require_authenticated_user),
) -> EvalRun:
    service = _get_service(request)
    try:
        run = await service.start_run(
            use_case=PRIMARY_USE_CASE,
            mode=body.mode,
            scenario_names=body.scenarios or None,
            started_by=user.get("userId", "anonymous"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to start ARE eval run")
        raise HTTPException(status_code=500, detail=f"Failed to start eval run: {exc}") from exc
    return run


@router.get("/runs", response_model=EvalRunList)
async def list_runs(request: Request, limit: int = 50) -> EvalRunList:
    service = _get_service(request)
    runs = await service.list_runs(PRIMARY_USE_CASE, limit=limit)
    return EvalRunList(runs=runs)


@router.get("/runs/{run_id}", response_model=EvalRun)
async def get_run(run_id: str, request: Request) -> EvalRun:
    service = _get_service(request)
    run = await service.get_run(PRIMARY_USE_CASE, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run
