from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class V2Error(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = {} if details is None else details
        self.headers = {} if headers is None else headers


def install_v2_error_contract(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        if not _is_v2_path(request.url.path):
            return await call_next(request)
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            response = _error_response(
                request,
                "internal_error",
                "Internal server error",
                500,
                {},
            )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(V2Error)
    async def handle_v2_error(request: Request, exc: V2Error) -> JSONResponse:
        return _error_response(
            request, exc.code, exc.message, exc.status_code, exc.details, exc.headers
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        if not _is_v2_path(request.url.path):
            return await http_exception_handler(request, exc)
        code, message = {
            404: ("not_found", "Resource not found"),
            405: ("method_not_allowed", "Method not allowed"),
        }.get(exc.status_code, ("http_error", "Request failed"))
        return _error_response(request, code, message, exc.status_code, {})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        if not _is_v2_path(request.url.path):
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
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "details": details,
        "requestId": request_id,
    }
    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=response_headers,
    )


def _is_v2_path(path: str) -> bool:
    return path == "/api/v2" or path.startswith("/api/v2/")
