"""Serve o dashboard HTML estático."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import pathlib

router = APIRouter()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    html_path = pathlib.Path(__file__).parent.parent / "static" / "dashboard.html"
    return HTMLResponse(html_path.read_text())
