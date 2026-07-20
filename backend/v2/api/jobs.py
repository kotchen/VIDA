from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..container import V2Runtime
from ..schemas import JobResponse
from .episodes import _job_response


def create_job_router(runtime: V2Runtime) -> APIRouter:
    router = APIRouter(prefix="/jobs", tags=["jobs"])

    @router.get("/{job_id}", response_model=JobResponse)
    def get_job(job_id: str):
        service = runtime.require_episodes()
        job = service.get_job(job_id)
        return _job_response(job, service.queue_position(job.id))

    @router.post("/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str):
        service = runtime.require_episodes()
        job = service.cancel_job(job_id)
        return JSONResponse(
            status_code=200 if job.status == "canceled" else 202,
            content=_job_response(job, service.queue_position(job.id)).model_dump(by_alias=True),
        )

    return router
