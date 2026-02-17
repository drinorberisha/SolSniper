"""
discovery.py — Router for the Smart Wallet Discovery UI.

Provides endpoints for:
- Viewing discovered winning tokens
- Viewing smart wallet candidates
- Manually triggering discovery
- Promoting candidates to tracked wallets
- Configuring discovery parameters
"""

import asyncio
from fastapi import APIRouter, Request, Depends, Form, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, func
from typing import Annotated

from app.database import get_session
from app.models import (
    DiscoveredToken,
    EarlyBuyer,
    SmartWalletCandidate,
    TrackedWallet,
    WalletStatus,
)
from app.tasks.wallet_discovery import run_discovery

router = APIRouter(prefix="/discovery", tags=["discovery"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/tokens", response_class=HTMLResponse)
async def get_discovered_tokens(
    request: Request, session: Session = Depends(get_session)
):
    """Return the list of discovered winning tokens."""
    tokens = session.exec(
        select(DiscoveredToken).order_by(DiscoveredToken.gain_multiple.desc())
    ).all()
    return templates.TemplateResponse(
        "partials/discovery_tokens.html",
        {"request": request, "tokens": tokens},
    )


@router.get("/candidates", response_class=HTMLResponse)
async def get_smart_candidates(
    request: Request, session: Session = Depends(get_session)
):
    """Return the list of smart wallet candidates."""
    candidates = session.exec(
        select(SmartWalletCandidate).order_by(SmartWalletCandidate.token_count.desc())
    ).all()
    return templates.TemplateResponse(
        "partials/discovery_candidates.html",
        {"request": request, "candidates": candidates},
    )


@router.get("/status", response_class=HTMLResponse)
async def get_discovery_status(
    request: Request, session: Session = Depends(get_session)
):
    """Return a summary status of the discovery system."""
    token_count = session.exec(select(func.count()).select_from(DiscoveredToken)).one()
    candidate_count = session.exec(
        select(func.count()).select_from(SmartWalletCandidate)
    ).one()
    promoted_count = session.exec(
        select(func.count())
        .select_from(SmartWalletCandidate)
        .where(SmartWalletCandidate.is_promoted == True)
    ).one()

    return templates.TemplateResponse(
        "partials/discovery_status.html",
        {
            "request": request,
            "token_count": token_count,
            "candidate_count": candidate_count,
            "promoted_count": promoted_count,
        },
    )


@router.post("/run", response_class=HTMLResponse)
async def trigger_discovery(
    request: Request,
    background_tasks: BackgroundTasks,
    min_gain: Annotated[float, Form()] = 100,
    lookback_days: Annotated[int, Form()] = 7,
    early_buyers: Annotated[int, Form()] = 50,
    auto_promote: Annotated[bool, Form()] = False,
):
    """Manually trigger a discovery run."""
    background_tasks.add_task(
        _run_discovery_wrapper,
        min_gain=min_gain,
        lookback_days=lookback_days,
        early_buyer_count=early_buyers,
        auto_promote=auto_promote,
    )
    return HTMLResponse(
        '<div class="text-green-400 text-sm py-2">'
        "Discovery started! Refresh in a minute to see results."
        "</div>"
    )


async def _run_discovery_wrapper(**kwargs):
    """Wrapper to run discovery in background."""
    try:
        await run_discovery(**kwargs)
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Discovery error: {e}", exc_info=True)


@router.post("/promote/{wallet_address}", response_class=HTMLResponse)
async def promote_candidate(
    request: Request,
    wallet_address: str,
    session: Session = Depends(get_session),
):
    """Promote a smart wallet candidate to tracked wallets."""
    candidate = session.exec(
        select(SmartWalletCandidate).where(
            SmartWalletCandidate.wallet_address == wallet_address
        )
    ).first()

    if candidate and not candidate.is_promoted:
        existing = session.get(TrackedWallet, wallet_address)
        if not existing:
            wallet = TrackedWallet(
                address=wallet_address,
                label=f"Discovery_{candidate.token_count}x ({candidate.token_symbols[:25]})",
                status=WalletStatus.active,
                source="discovery",
            )
            session.add(wallet)

        candidate.is_promoted = True
        session.add(candidate)
        session.commit()

    return await get_smart_candidates(request, session)


@router.post("/promote-all", response_class=HTMLResponse)
async def promote_all_candidates(
    request: Request,
    session: Session = Depends(get_session),
):
    """Promote all unpromoted smart wallet candidates."""
    candidates = session.exec(
        select(SmartWalletCandidate).where(SmartWalletCandidate.is_promoted == False)
    ).all()

    for c in candidates:
        existing = session.get(TrackedWallet, c.wallet_address)
        if not existing:
            wallet = TrackedWallet(
                address=c.wallet_address,
                label=f"Discovery_{c.token_count}x ({c.token_symbols[:25]})",
                status=WalletStatus.active,
                source="discovery",
            )
            session.add(wallet)
        c.is_promoted = True
        session.add(c)

    session.commit()
    return await get_smart_candidates(request, session)
