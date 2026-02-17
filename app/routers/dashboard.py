from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from typing import Annotated

from app.database import get_session
from app.models import Signal, Token, TrackedWallet, WalletStatus

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/signals/latest", response_class=HTMLResponse)
async def get_latest_signals(request: Request, session: Session = Depends(get_session)):
    # Fetch latest 20 signals with their token info
    statement = (
        select(Signal, Token)
        .join(Token, Signal.token_address == Token.contract_address, isouter=True)
        .order_by(Signal.timestamp.desc())
        .limit(20)
    )
    rows = session.exec(statement).all()
    # Build a list of dicts so templates can access both signal and token fields
    enriched = []
    for signal, token in rows:
        enriched.append(
            {
                "signal": signal,
                "token": token,
            }
        )

    # Render partial template
    return templates.TemplateResponse(
        "partials/signal_list.html", {"request": request, "entries": enriched}
    )


@router.get("/wallets", response_class=HTMLResponse)
async def get_wallets(request: Request, session: Session = Depends(get_session)):
    statement = select(TrackedWallet).order_by(TrackedWallet.label)
    wallets = session.exec(statement).all()
    return templates.TemplateResponse(
        "partials/wallet_list.html", {"request": request, "wallets": wallets}
    )


@router.post("/wallets", response_class=HTMLResponse)
async def add_wallet(
    request: Request,
    address: Annotated[str, Form()],
    label: Annotated[str, Form()],
    session: Session = Depends(get_session),
):
    try:
        wallet = TrackedWallet(address=address, label=label, status=WalletStatus.active)
        session.add(wallet)
        session.commit()
    except Exception as e:
        session.rollback()
        # In a real app, handle error (e.g. duplicate key)
        pass

    # Return updated list
    return await get_wallets(request, session)


@router.delete("/wallets/{address}", response_class=HTMLResponse)
async def delete_wallet(
    request: Request, address: str, session: Session = Depends(get_session)
):
    wallet = session.get(TrackedWallet, address)
    if wallet:
        session.delete(wallet)
        session.commit()
    return await get_wallets(request, session)
