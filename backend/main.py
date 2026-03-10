import logging
import time
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.config import settings
from backend.database import init_db
from backend.api.rfq import router as rfq_router


# -------------------------------------------------------
# Structured JSON logging
# -------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Output log records as single-line JSON for structured log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        extra_keys = ("request_id", "method", "path", "status_code", "duration_ms")
        for key in extra_keys:
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        return json.dumps(log_entry, ensure_ascii=False)


def _setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    # Quiet noisy libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger(__name__)


# -------------------------------------------------------
# Request logging middleware
# -------------------------------------------------------

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request/response with timing and request ID."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        start = time.perf_counter()

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Khoi tao database khi server khoi dong."""
    init_db()
    logger.info("Database initialized")
    logger.info("%s is running", settings.app_name)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="AI-Powered RFQ Automation System with Real Email Integration",
    lifespan=lifespan,
)

# Rate limiter — 60 requests/minute per IP
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )

# CORS - chi cho phep frontend va localhost truy cap
CORS_ORIGINS = [
    "http://localhost:8501",       # Streamlit local
    "http://frontend:8501",        # Streamlit trong Docker network
    "http://localhost:8000",       # Backend (Swagger UI)
    "http://127.0.0.1:8501",
    "http://127.0.0.1:8000",
    "https://rfq-project.onrender.com",            # Render frontend
    "https://rfq-project-frontend.onrender.com",   # Render frontend (alt name)
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# Register routers
app.include_router(rfq_router)


@app.get("/health")
def health_check():
    """Health check endpoint for Docker and monitoring."""
    return {"status": "healthy", "service": settings.app_name}
