"""Authentication router for the web UI."""

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer
from starlette.responses import Response

from potluck.core.config import get_settings
from potluck.web.dependencies import SESSION_MAX_AGE

router = APIRouter(tags=["auth"])


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None) -> Response:
    """Render the login page."""
    settings = get_settings()
    if not settings.web_password:
        return RedirectResponse(url="/", status_code=303)

    templates = request.app.state.templates
    return templates.TemplateResponse(  # type: ignore[no-any-return]
        request,
        "pages/login.html",
        {"error": error},
    )


@router.post("/login")
async def login(request: Request, password: str = Form(...)) -> Response:
    """Verify password and set signed session cookie."""
    settings = get_settings()

    if not settings.web_password:
        return RedirectResponse(url="/", status_code=303)

    if not hmac.compare_digest(password, settings.web_password):
        templates = request.app.state.templates
        return templates.TemplateResponse(  # type: ignore[no-any-return]
            request,
            "pages/login.html",
            {"error": "Invalid password"},
            status_code=401,
        )

    serializer = URLSafeTimedSerializer(settings.web_secret_key)
    token = serializer.dumps("authenticated")

    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return response


@router.get("/logout")
async def logout() -> RedirectResponse:
    """Clear session cookie and redirect to login."""
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session_token")
    return response
