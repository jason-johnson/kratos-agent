"""Health check endpoints."""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "service": "kratos-agent-service"}


@router.get("/health/ready")
async def readiness_check(request: Request) -> dict[str, str]:
    if not getattr(request.app.state, "ready", False):
        raise HTTPException(status_code=503, detail="Service initialization is incomplete")
    return {"status": "ready"}
