from time import perf_counter
from uuid import uuid4

from fastapi import Request
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        started = perf_counter()

        with logger.contextualize(request_id=request_id):
            response = await call_next(request)
            elapsed_ms = (perf_counter() - started) * 1000
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "{method} {path} -> {status} in {elapsed:.2f} ms",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                elapsed=elapsed_ms,
            )
            return response
