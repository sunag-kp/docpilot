from __future__ import annotations

from pathlib import Path

import httpx
from asgi_lifespan import LifespanManager

from docpilot.api.app import create_app
from docpilot.config import Settings
from docpilot.container import Container


async def test_built_ui_is_served_without_shadowing_the_api(
    settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<html>DocPilot UI</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")

    async def factory(s: Settings) -> Container:
        return Container(settings=s)

    app = create_app(settings.model_copy(update={"ui_dist_path": str(tmp_path)}), factory)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert "DocPilot UI" in (await client.get("/")).text
            assert (await client.get("/assets/app.js")).status_code == 200
            assert (await client.get("/healthz")).json()["status"] == "ok"
            assert (await client.get("/openapi.json")).status_code == 200
