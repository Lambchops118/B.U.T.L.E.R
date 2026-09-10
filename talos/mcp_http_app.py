from __future__ import annotations

import contextlib

from starlette.applications import Starlette
from starlette.routing import Mount

from talos.mcp_servers.home_automation_server import create_home_automation_server
from talos.mcp_servers.tv_control_server import create_tv_control_server


home_automation_mcp = create_home_automation_server()
tv_control_mcp = create_tv_control_server()

# Mount each server at the root of its own path so the final endpoints are:
#   /home-automation
#   /tv-control
home_automation_mcp.settings.streamable_http_path = "/"
tv_control_mcp.settings.streamable_http_path = "/"


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(home_automation_mcp.session_manager.run())
        await stack.enter_async_context(tv_control_mcp.session_manager.run())
        yield


app = Starlette(
    routes=[
        Mount("/home-automation", app=home_automation_mcp.streamable_http_app()),
        Mount("/tv-control", app=tv_control_mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)
