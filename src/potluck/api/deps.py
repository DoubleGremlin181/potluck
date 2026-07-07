"""Shared FastAPI dependencies for the REST routers."""

from typing import Annotated, cast

from fastapi import Depends, Request

from potluck.services.context import AppContext


def get_context(request: Request) -> AppContext:
    """The AppContext the app factory stowed on ``app.state.context``."""
    return cast(AppContext, request.app.state.context)


CtxDep = Annotated[AppContext, Depends(get_context)]
