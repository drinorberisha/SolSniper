"""
price_bot.py — Periodically updates market caps and token statuses.

Uses DexScreener API (free, no key) to fetch current prices:
- Updates market_cap_at_scan on Token rows
- Detects graduated tokens (listed on Raydium/Meteora with >$50k MC)
- Detects rugged tokens (MC drops below $1k or pair disappears)
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from app.database import engine
from app.models import Token, TokenStatus

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com"

# Thresholds
GRADUATED_MC = 50_000  # $50k MC = likely graduated from bonding curve
RUGGED_MC = 500  # <$500 MC = likely rugged
STALE_HOURS = 48  # Stop updating tokens older than 48h


async def _fetch_token_data(
    client: httpx.AsyncClient, addresses: list[str]
) -> dict[str, dict]:
    """
    Fetch current pair data for up to 30 tokens from DexScreener.
    Returns {token_address: best_pair_data}.
    """
    result: dict[str, dict] = {}
    for i in range(0, len(addresses), 30):
        batch = addresses[i : i + 30]
        addr_str = ",".join(batch)
        try:
            resp = await client.get(f"{DEXSCREENER_API}/latest/dex/tokens/{addr_str}")
            resp.raise_for_status()
            data = resp.json()
            pairs = data.get("pairs") or []

            # Keep the highest-MC pair per token
            for pair in pairs:
                token_addr = pair.get("baseToken", {}).get("address", "")
                if not token_addr:
                    continue
                mc = pair.get("marketCap") or pair.get("fdv") or 0
                existing = result.get(token_addr)
                if not existing or mc > (
                    existing.get("marketCap") or existing.get("fdv") or 0
                ):
                    result[token_addr] = pair
        except Exception as e:
            logger.debug(f"DexScreener batch fetch error: {e}")

        if i + 30 < len(addresses):
            await asyncio.sleep(0.5)  # Rate limit

    return result


def _determine_status(pair: dict | None, current_status: TokenStatus) -> TokenStatus:
    """Determine token status from pair data."""
    if pair is None:
        # No pair data found — might be too new or rugged
        return current_status

    mc = pair.get("marketCap") or pair.get("fdv") or 0
    dex = pair.get("dexId", "").lower()
    liquidity = pair.get("liquidity", {}).get("usd", 0) or 0

    # Rugged: very low MC or zero liquidity
    if mc < RUGGED_MC or (mc > 0 and liquidity < 100):
        return TokenStatus.rugged

    # Graduated: listed on major DEX with decent MC
    graduated_dexes = {"raydium", "meteora", "orca", "jupiter"}
    if dex in graduated_dexes and mc >= GRADUATED_MC:
        return TokenStatus.graduated

    # High MC even on pumpfun = effectively graduated
    if mc >= GRADUATED_MC * 2:
        return TokenStatus.graduated

    return current_status


async def price_updater():
    """
    Background task: every 60s, update market caps and statuses
    for active tokens (bonding_curve or graduated, within 48h).
    """
    logger.info("Price Bot started")

    # Wait for app to initialize
    await asyncio.sleep(10)

    while True:
        try:
            with Session(engine) as session:
                # Get tokens that are still active (not rugged)
                tokens = session.exec(
                    select(Token).where(
                        Token.status.in_(
                            [
                                TokenStatus.bonding_curve,
                                TokenStatus.graduated,
                            ]
                        )
                    )
                ).all()

                if not tokens:
                    await asyncio.sleep(60)
                    continue

                # Filter out stale tokens (>48h old)
                now = datetime.now(timezone.utc)
                active_tokens = []
                for t in tokens:
                    age_hours = (
                        now - t.created_at.replace(tzinfo=timezone.utc)
                    ).total_seconds() / 3600
                    if age_hours <= STALE_HOURS:
                        active_tokens.append(t)

                if not active_tokens:
                    await asyncio.sleep(60)
                    continue

                addresses = [t.contract_address for t in active_tokens]
                logger.info(f"Price Bot: updating {len(addresses)} tokens")

                async with httpx.AsyncClient(timeout=30) as client:
                    pair_data = await _fetch_token_data(client, addresses)

                updated = 0
                for token in active_tokens:
                    pair = pair_data.get(token.contract_address)
                    if pair:
                        new_mc = pair.get("marketCap") or pair.get("fdv") or 0
                        if new_mc > 0:
                            token.market_cap_at_scan = new_mc
                            updated += 1

                    new_status = _determine_status(pair, token.status)
                    if new_status != token.status:
                        logger.info(
                            f"  {token.symbol}: {token.status.value} → {new_status.value} "
                            f"(MC: ${token.market_cap_at_scan:,.0f})"
                        )
                        token.status = new_status

                    session.add(token)

                session.commit()
                if updated:
                    logger.info(
                        f"Price Bot: updated {updated}/{len(active_tokens)} prices"
                    )

        except Exception as e:
            logger.error(f"Price Bot error: {e}", exc_info=True)

        await asyncio.sleep(60)
