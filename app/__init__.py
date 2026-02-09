import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from config import ALLOWED_ORIGINS, DEBUG, DOCS, XRAY_SUBSCRIPTION_PATH

__version__ = "0.8.4"

app = FastAPI(
    title="MarzbanAPI",
    description="Unified GUI Censorship Resistant Solution Powered by Xray",
    version=__version__,
    docs_url="/docs" if DOCS else None,
    redoc_url="/redoc" if DOCS else None,
)

scheduler = BackgroundScheduler(
    {"apscheduler.job_defaults.max_instances": 20}, timezone="UTC"
)
logger = logging.getLogger("uvicorn.error")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Timing middleware — logs slow requests
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    # Add timing header
    response.headers["X-Process-Time"] = f"{duration:.4f}"

    # Log slow requests (>500ms)
    if duration > 0.5:
        logger.warning(f"Slow request: {request.method} {request.url.path} took {duration:.2f}s")

    return response


# Optional: pyinstrument profiling middleware (only in DEBUG mode)
if DEBUG:
    try:
        from pyinstrument import Profiler

        @app.middleware("http")
        async def pyinstrument_middleware(request: Request, call_next):
            # Only profile /api/user endpoints
            if "/api/user" not in request.url.path:
                return await call_next(request)

            profiler = Profiler(async_mode="enabled")
            profiler.start()
            response = await call_next(request)
            profiler.stop()

            # Log profile for slow requests (>100ms)
            duration = float(response.headers.get("X-Process-Time", "0"))
            if duration > 0.1:
                logger.info(f"Profile for {request.method} {request.url.path}:\n{profiler.output_text(unicode=True, color=False)}")

            return response

        logger.info("pyinstrument profiling middleware enabled")
    except ImportError:
        pass  # pyinstrument not installed


from app import dashboard, jobs, routers, telegram  # noqa
from app.routers import api_router  # noqa

app.include_router(api_router)


def use_route_names_as_operation_ids(app: FastAPI) -> None:
    for route in app.routes:
        if isinstance(route, APIRoute):
            route.operation_id = route.name


use_route_names_as_operation_ids(app)


@app.on_event("startup")
def on_startup():
    paths = [f"{r.path}/" for r in app.routes]
    paths.append("/api/")
    if f"/{XRAY_SUBSCRIPTION_PATH}/" in paths:
        raise ValueError(
            f"you can't use /{XRAY_SUBSCRIPTION_PATH}/ as subscription path it reserved for {app.title}"
        )
    scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown()


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = {}
    for error in exc.errors():
        details[error["loc"][-1]] = error.get("msg")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder({"detail": details}),
    )
