from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class V2Error(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = {} if details is None else details


def install_v2_error_contract(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        if not request.url.path.startswith("/api/v2"):
            return await call_next(request)
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(V2Error)
    async def handle_v2_error(request: Request, exc: V2Error) -> JSONResponse:
        return _error_response(request, exc.code, exc.message, exc.status_code, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith("/api/v2"):
            return await request_validation_exception_handler(request, exc)
        errors = [
            {
                "type": error.get("type", "validation_error"),
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Invalid value"),
            }
            for error in exc.errors()
        ]
        return _error_response(
            request,
            "validation_error",
            "Request validation failed",
            422,
            {"errors": errors},
        )


def _error_response(
    request: Request,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any],
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "requestId": request_id,
    }
    if details:
        error["details"] = details
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers={"X-Request-ID": request_id},
    )
