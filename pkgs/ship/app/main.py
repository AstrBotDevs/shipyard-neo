from fastapi import FastAPI
from contextlib import asynccontextmanager
from .components.filesystem import router as fs_router
from .components.ipython import router as ipython_router
from .components.shell import router as shell_router
from .components.upload import router as upload_router
from .components.term import router as term_router
from .components.user_manager import UserManager
import logging
import tomli
from pathlib import Path

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("Starting Ship container initialization...")
    restored_count = await UserManager.restore_all_users()
    logger.info(f"User restoration completed: {restored_count} users restored")
    yield
    logger.info("Ship container shutting down")


app = FastAPI(
    title="Ship API",
    description="A containerized execution environment with filesystem, IPython, and shell capabilities",
    version="1.0.0",
    lifespan=lifespan,
)

# Include component routers
app.include_router(fs_router, prefix="/fs", tags=["filesystem"])
app.include_router(ipython_router, prefix="/ipython", tags=["ipython"])
app.include_router(shell_router, prefix="/shell", tags=["shell"])
app.include_router(upload_router, tags=["upload"])
app.include_router(term_router, prefix="/term", tags=["terminal"])


@app.get("/")
async def root():
    return {"message": "Ship API is running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


def get_version() -> str:
    """Get version from pyproject.toml"""
    try:
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            data = tomli.load(f)
        return data.get("project", {}).get("version", "unknown")
    except Exception:
        return "unknown"


@app.get("/stat")
async def get_stat():
    """Get service statistics and version information"""
    return {
        "service": "ship",
        "version": get_version(),
        "status": "running",
        "author": "AstrBot Team",
    }
