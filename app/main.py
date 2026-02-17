import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import SQLModel
from sqlalchemy import text

from app.database import engine
from app import models
from app.routers import dashboard, discovery
from app.tasks.listener import listen_for_new_tokens
from app.tasks.wallet_discovery import run_discovery_background
from app.tasks.price_bot import price_updater

logger = logging.getLogger(__name__)


def _run_migrations():
    """
    Add any columns that exist in the models but are missing from the DB.
    SQLModel's create_all only creates new tables, not new columns.
    """
    migrations = [
        (
            "tracked_wallets",
            "source",
            "ALTER TABLE tracked_wallets ADD COLUMN source VARCHAR DEFAULT NULL",
        ),
        (
            "tracked_wallets",
            "tracked_at",
            "ALTER TABLE tracked_wallets ADD COLUMN tracked_at TIMESTAMP DEFAULT NOW()",
        ),
    ]
    with engine.connect() as conn:
        for table, column, ddl in migrations:
            try:
                conn.execute(text(f"SELECT {column} FROM {table} LIMIT 0"))
            except Exception:
                conn.rollback()
                logger.info(f"Adding missing column {table}.{column}")
                conn.execute(text(ddl))
                conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creating tables on startup
    SQLModel.metadata.create_all(engine)

    # Run lightweight column migrations
    _run_migrations()

    # Start the background listener task
    listener_task = asyncio.create_task(listen_for_new_tokens())

    # Start the smart-wallet discovery background loop
    discovery_task = asyncio.create_task(run_discovery_background())

    # Start the price update bot
    price_task = asyncio.create_task(price_updater())

    yield

    # Cancel tasks on shutdown
    listener_task.cancel()
    discovery_task.cancel()
    price_task.cancel()


app = FastAPI(title="Solana Sniper Stack", lifespan=lifespan)

# Mount routers
app.include_router(dashboard.router)
app.include_router(discovery.router)

# We can mount static files if we have any custom CSS/JS
# app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates are used in routers
templates = Jinja2Templates(directory="app/templates")
