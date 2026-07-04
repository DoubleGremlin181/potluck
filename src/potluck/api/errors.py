"""Uniform JSON error envelope for the REST API (#131).

Every ``/api/*`` error response — service errors, request validation, and
router-level HTTP errors alike — has the shape
``{"error": {"code": "<machine_code>", "message": "<human>"}}`` (plus
``error.detail`` on validation failures). Handlers are registered by the app
factory and never leak stack traces to clients; non-API paths (the SPA
mount) keep FastAPI's default behaviour.
"""

from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, JsonValue
from starlette.exceptions import HTTPException as StarletteHTTPException

from potluck.core.errors import InvalidCursorError, ItemNotFoundError


class ErrorDetail(BaseModel):
    """The error object inside the envelope."""

    code: str = Field(description="Machine-readable error code, e.g. 'item_not_found'.")
    message: str = Field(description="Human-readable explanation.")
    detail: list[JsonValue] | None = Field(
        default=None,
        description="Field-level validation errors (present on 422 responses only).",
    )


class ErrorEnvelope(BaseModel):
    """Uniform body of every /api/* error response."""

    error: ErrorDetail


_RESPONSE_DESCRIPTIONS = {
    400: "Malformed pagination cursor, or a cursor produced by a different query.",
    404: "No item with this id exists.",
    422: "Request validation failed; `error.detail` lists the offending parameters.",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries documenting enveloped error statuses."""
    return {
        status: {"model": ErrorEnvelope, "description": _RESPONSE_DESCRIPTIONS[status]}
        for status in statuses
    }


def _envelope(
    status_code: int, code: str, message: str, detail: list[JsonValue] | None = None
) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorDetail(code=code, message=message, detail=detail))
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


# Machine codes for router-level HTTP errors on /api/* paths.
_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def register_error_handlers(app: FastAPI) -> None:
    """Map exceptions onto the envelope (called once by the app factory)."""

    @app.exception_handler(ItemNotFoundError)
    def item_not_found(request: Request, exc: ItemNotFoundError) -> JSONResponse:
        return _envelope(404, "item_not_found", str(exc))

    @app.exception_handler(InvalidCursorError)
    def invalid_cursor(request: Request, exc: InvalidCursorError) -> JSONResponse:
        return _envelope(400, "invalid_cursor", str(exc))

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's per-field detail survives, inside the envelope.
        return _envelope(
            422, "validation_error", "Request validation failed.", jsonable_encoder(exc.errors())
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if request.url.path.startswith("/api/"):
            code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
            return _envelope(exc.status_code, code, str(exc.detail))
        # SPA/static paths keep the default handler (plain-text/HTML 404s).
        return await http_exception_handler(request, exc)
