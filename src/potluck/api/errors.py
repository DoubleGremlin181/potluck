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
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, JsonValue
from starlette.exceptions import HTTPException as StarletteHTTPException

from potluck.core.errors import (
    ImportInProgressError,
    ImportNotFoundError,
    InvalidCursorError,
    ItemNotFoundError,
    UnsupportedArchiveError,
)


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
    409: "An import is already running; only one runs at a time.",
    422: "Request validation failed; `error.detail` lists the offending parameters.",
}


def error_responses(
    *statuses: int, overrides: dict[int, str] | None = None
) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses`` entries documenting enveloped error statuses.

    ``overrides`` replaces the default description for a status where the
    endpoint's failure mode differs (e.g. a 404 that is about imports, not
    items).
    """
    descriptions = _RESPONSE_DESCRIPTIONS | (overrides or {})
    return {
        status: {"model": ErrorEnvelope, "description": descriptions[status]} for status in statuses
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

    @app.exception_handler(ImportNotFoundError)
    def import_not_found(request: Request, exc: ImportNotFoundError) -> JSONResponse:
        return _envelope(404, "import_not_found", str(exc))

    @app.exception_handler(ImportInProgressError)
    def import_in_progress(request: Request, exc: ImportInProgressError) -> JSONResponse:
        return _envelope(409, "import_in_progress", str(exc))

    @app.exception_handler(UnsupportedArchiveError)
    def unsupported_archive(request: Request, exc: UnsupportedArchiveError) -> JSONResponse:
        return _envelope(400, "unsupported_archive", str(exc))

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

    @app.exception_handler(Exception)
    def unhandled_error(request: Request, exc: Exception) -> Response:
        # Catch-all: starlette re-raises after this response is sent, so
        # servers still log the full traceback — but clients only ever get
        # the generic envelope, never the exception text (no internal detail
        # can leak).
        if request.url.path.startswith("/api/"):
            return _envelope(500, "internal_error", "Internal server error.")
        return PlainTextResponse("Internal Server Error", status_code=500)
