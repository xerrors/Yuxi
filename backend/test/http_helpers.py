"""真实 TCP HTTP 测试的服务生命周期。"""

import asyncio
import socket
from contextlib import asynccontextmanager

import uvicorn


@asynccontextmanager
async def live_http_server(app):
    """启动临时本地服务，结束后关闭监听与连接。"""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("Test HTTP server did not start")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=5)
        listener.close()
