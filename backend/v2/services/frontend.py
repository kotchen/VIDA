from __future__ import annotations

from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def install_v2_frontend(app: FastAPI, dist_dir: Path) -> bool:
    dist = Path(dist_dir)
    index = dist / "index.html"
    if not index.is_file():
        return False

    assets = dist / "assets"
    if assets.is_dir():
        app.mount(
            "/v2/assets",
            StaticFiles(directory=str(assets), check_dir=True),
            name="v2-assets",
        )

    def index_response() -> FileResponse:
        return FileResponse(
            index,
            media_type="text/html",
            headers={"Cache-Control": "no-cache"},
        )

    @app.api_route(
        "/v2",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def v2_root():
        return index_response()

    @app.api_route(
        "/v2/{client_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def v2_client_route(client_path: str):
        if not _is_client_route(client_path):
            raise HTTPException(status_code=404, detail="Not Found")
        return index_response()

    return True


def _is_client_route(client_path: str) -> bool:
    if not client_path or "\x00" in client_path or "\\" in client_path:
        return False
    path = PurePosixPath(client_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if path.parts[0] == "assets":
        return False
    return all("." not in part for part in path.parts)
