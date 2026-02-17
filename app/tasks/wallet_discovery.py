"""
wallet_discovery.py — Automated Smart Wallet Discovery Engine.

Finds top-performing Solana tokens from the past week across multiple sources
(DexScreener, Helius), extracts the earliest buyers, and identifies wallets
that appear across multiple winners ("Smart Money").

Pipeline:
  1. Discover winning tokens (>200x gain) from DexScreener
  2. For each winner, extract earliest buyers via Helius Enhanced API
  3. Cross-reference: wallets appearing in 2+ winners = Smart Money candidates
  4. Optionally auto-promote to tracked_wallets
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from sqlmodel import Session, select, text

from app.config import settings
from app.database import engine
from app.models import (
    DiscoveredToken,
    DiscoveryStatus,
    EarlyBuyer,
    SmartWalletCandidate,
    TrackedWallet,
    WalletStatus,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Minimum gain multiple to consider a token a "winner" (200x = 20,000%)
MIN_GAIN_MULTIPLE = 200
# How many early buyers to extract per winning token
DEFAULT_EARLY_BUYER_COUNT = 50
# How many days back to look for winners
LOOKBACK_DAYS = 7
# Minimum appearances across different winners to be considered "smart"
MIN_APPEARANCES = 2
# DexScreener API base
DEXSCREENER_API = "https://api.dexscreener.com"
# Helius Enhanced API
HELIUS_TX_API = f"{settings.HELIUS_REST_URL}/addresses"


# ──────────────────────────────────────────────
# Step 1: Discover Winning Tokens
# ──────────────────────────────────────────────


async def _fetch_boosted_tokens(client: httpx.AsyncClient) -> list[dict]:
    """Fetch top boosted tokens from DexScreener (Solana only)."""
    try:
        resp = await client.get(f"{DEXSCREENER_API}/token-boosts/top/v1")
        resp.raise_for_status()
        data = resp.json()
        return [t for t in data if t.get("chainId") == "solana"]
    except Exception as e:
        logger.error(f"DexScreener boosted fetch failed: {e}")
        return []


async def _fetch_token_pairs(
    client: httpx.AsyncClient, addresses: list[str]
) -> list[dict]:
    """
    Fetch pair data for multiple tokens from DexScreener.
    Returns the highest-MC pair per token.
    DexScreener allows up to 30 addresses per call.
    """
    all_pairs = []
    # Batch in groups of 30
    for i in range(0, len(addresses), 30):
        batch = addresses[i : i + 30]
        addr_str = ",".join(batch)
        try:
            resp = await client.get(f"{DEXSCREENER_API}/latest/dex/tokens/{addr_str}")
            resp.raise_for_status()
            data = resp.json()
            pairs = data.get("pairs", [])
            all_pairs.extend(pairs)
        except Exception as e:
            logger.error(f"DexScreener token fetch failed: {e}")
        if i + 30 < len(addresses):
            await asyncio.sleep(0.5)
    return all_pairs


async def _search_solana_tokens(
    client: httpx.AsyncClient, queries: list[str]
) -> list[dict]:
    """
    Search DexScreener for Solana tokens using various search terms.
    Returns unique token pairs.
    """
    seen = set()
    results = []
    for q in queries:
        try:
            resp = await client.get(
                f"{DEXSCREENER_API}/latest/dex/search",
                params={"q": q},
            )
            resp.raise_for_status()
            data = resp.json()
            for pair in data.get("pairs", []):
                if pair.get("chainId") != "solana":
                    continue
                addr = pair.get("baseToken", {}).get("address", "")
                if addr and addr not in seen:
                    seen.add(addr)
                    results.append(pair)
        except Exception as e:
            logger.debug(f"DexScreener search '{q}' failed: {e}")
        await asyncio.sleep(0.3)
    return results


def _estimate_gain(pair: dict) -> float:
    """
    Estimate the gain multiple for a token pair.
    Uses FDV/MC vs estimated launch price.
    Pump.fun tokens start at ~$5k MC.
    """
    mc = pair.get("marketCap") or pair.get("fdv") or 0
    if mc <= 0:
        return 0.0

    # Use pair creation timestamp to estimate if it's a pump.fun token
    dex = pair.get("dexId", "")
    created = pair.get("pairCreatedAt", 0)

    # For pump.fun/pumpswap tokens, launch MC is ~$5k
    if "pump" in dex.lower():
        launch_mc = 5000.0
    else:
        # For other DEXes, estimate launch at $10k
        launch_mc = 10000.0

    return mc / launch_mc


async def discover_winning_tokens(
    min_gain: float = MIN_GAIN_MULTIPLE,
    lookback_days: int = LOOKBACK_DAYS,
) -> list[dict]:
    """
    Find tokens that achieved massive gains in the last N days.

    Sources:
    1. DexScreener: top boosted tokens + search queries
    2. Filters for Solana, min gain, age

    Returns list of dicts with token info.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    cutoff_ms = int(cutoff.timestamp() * 1000)

    async with httpx.AsyncClient(timeout=30) as client:
        # Source 1: Top boosted tokens
        boosted = await _fetch_boosted_tokens(client)
        boosted_addrs = [t["tokenAddress"] for t in boosted]
        logger.info(f"Found {len(boosted_addrs)} boosted Solana tokens")

        # Source 2: Search with popular terms
        search_terms = [
            "pump SOL",
            "pumpfun",
            "meme SOL",
            "AI agent SOL",
            "cat SOL",
            "dog SOL",
            "pepe SOL",
            "trump SOL",
            "doge SOL",
            "bonk SOL",
        ]
        searched_pairs = await _search_solana_tokens(client, search_terms)
        searched_addrs = [
            p["baseToken"]["address"]
            for p in searched_pairs
            if p.get("baseToken", {}).get("address")
        ]
        logger.info(f"Found {len(searched_addrs)} tokens from search")

        # Combine and deduplicate addresses
        all_addrs = list(set(boosted_addrs + searched_addrs))
        logger.info(f"Total unique token addresses: {len(all_addrs)}")

        # Fetch detailed pair data for all tokens
        all_pairs = await _fetch_token_pairs(client, all_addrs)

        # Also include the searched pairs we already have
        seen_pair_addrs = {p.get("pairAddress") for p in all_pairs}
        for p in searched_pairs:
            if p.get("pairAddress") not in seen_pair_addrs:
                all_pairs.append(p)

        # Deduplicate: keep highest MC pair per token
        best_per_token: dict[str, dict] = {}
        for pair in all_pairs:
            token_addr = pair.get("baseToken", {}).get("address", "")
            if not token_addr:
                continue
            mc = pair.get("marketCap") or pair.get("fdv") or 0
            existing = best_per_token.get(token_addr)
            if not existing or mc > (
                existing.get("marketCap") or existing.get("fdv") or 0
            ):
                best_per_token[token_addr] = pair

        # Filter for winners
        winners = []
        for addr, pair in best_per_token.items():
            created = pair.get("pairCreatedAt", 0)
            if created and created < cutoff_ms:
                continue  # Too old

            gain = _estimate_gain(pair)
            if gain < min_gain:
                continue

            mc = pair.get("marketCap") or pair.get("fdv") or 0
            symbol = pair.get("baseToken", {}).get("symbol", "?")
            name = pair.get("baseToken", {}).get("name", "")
            dex = pair.get("dexId", "")

            winners.append(
                {
                    "address": addr,
                    "symbol": symbol,
                    "name": name,
                    "dex": dex,
                    "market_cap": mc,
                    "gain_multiple": round(gain, 1),
                    "pair_created_at": (
                        datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                        if created
                        else None
                    ),
                }
            )

        winners.sort(key=lambda w: w["gain_multiple"], reverse=True)
        logger.info(f"Found {len(winners)} winning tokens (>= {min_gain}x)")
        return winners


# ──────────────────────────────────────────────
# Step 2: Extract Early Buyers
# ──────────────────────────────────────────────


async def extract_early_buyers(
    token_address: str, limit: int = DEFAULT_EARLY_BUYER_COUNT
) -> list[dict]:
    """
    For a winning token, find the earliest unique buyers using Helius.

    Returns list of dicts: {wallet_address, entry_timestamp, tx_signature}
    sorted by time ascending (earliest first).
    """
    buyers: dict[str, dict] = {}  # wallet -> info (keep earliest)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            url = (
                f"{HELIUS_TX_API}/{token_address}/transactions"
                f"?api-key={settings.HELIUS_API_KEY}&limit=100"
            )
            resp = await client.get(url)
            resp.raise_for_status()
            txs = resp.json()

            if not isinstance(txs, list):
                logger.warning(f"Unexpected Helius response for {token_address}")
                return []

            # Sort oldest first
            txs.sort(key=lambda t: t.get("timestamp", 0))

            for tx in txs:
                fee_payer = tx.get("feePayer")
                if not fee_payer:
                    continue

                # Only count SWAP transactions (actual buys)
                tx_type = tx.get("type", "")
                source = tx.get("source", "")
                is_buy = tx_type == "SWAP" or source in (
                    "PUMP_FUN",
                    "PUMP_AMM",
                    "PUMP",
                )
                if not is_buy:
                    continue

                # Skip if we already have this wallet (keep earliest)
                if fee_payer in buyers:
                    continue

                ts = tx.get("timestamp", 0)
                entry_time = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None

                buyers[fee_payer] = {
                    "wallet_address": fee_payer,
                    "entry_timestamp": entry_time,
                    "tx_signature": tx.get("signature", ""),
                }

                if len(buyers) >= limit:
                    break

    except Exception as e:
        logger.error(f"Error extracting buyers for {token_address}: {e}")

    result = list(buyers.values())
    logger.info(f"Extracted {len(result)} early buyers for {token_address[:12]}...")
    return result


# ──────────────────────────────────────────────
# Step 3: Cross-Reference & Identify Smart Wallets
# ──────────────────────────────────────────────


def identify_smart_wallets(session: Session) -> list[dict]:
    """
    Find wallets that appear as early buyers across multiple winning tokens.
    These are the "Smart Money" candidates.
    """
    stmt = text(
        """
        SELECT
            eb.wallet_address,
            COUNT(DISTINCT eb.token_address) as token_count,
            STRING_AGG(DISTINCT dt.symbol, ', ') as token_symbols
        FROM early_buyers eb
        JOIN discovered_tokens dt ON dt.id = eb.token_id
        WHERE dt.status = 'done'
        GROUP BY eb.wallet_address
        HAVING COUNT(DISTINCT eb.token_address) >= :min_appearances
        ORDER BY token_count DESC
    """
    )
    results = session.exec(stmt, params={"min_appearances": MIN_APPEARANCES})

    candidates = []
    for row in results:
        candidates.append(
            {
                "wallet_address": row[0],
                "token_count": row[1],
                "token_symbols": row[2],
            }
        )

    logger.info(f"Identified {len(candidates)} smart wallet candidates")
    return candidates


def save_smart_wallet_candidates(session: Session, candidates: list[dict]) -> int:
    """Save or update smart wallet candidates in the DB."""
    saved = 0
    for c in candidates:
        existing = session.exec(
            select(SmartWalletCandidate).where(
                SmartWalletCandidate.wallet_address == c["wallet_address"]
            )
        ).first()

        if existing:
            existing.token_count = c["token_count"]
            existing.token_symbols = c["token_symbols"]
            session.add(existing)
        else:
            candidate = SmartWalletCandidate(
                wallet_address=c["wallet_address"],
                token_count=c["token_count"],
                token_symbols=c["token_symbols"],
            )
            session.add(candidate)
            saved += 1

    session.commit()
    return saved


def auto_promote_candidates(
    session: Session, min_appearances: int = MIN_APPEARANCES
) -> int:
    """
    Auto-promote smart wallet candidates to tracked_wallets.
    Only promotes candidates that aren't already tracked.
    """
    promoted = 0
    candidates = session.exec(
        select(SmartWalletCandidate).where(
            SmartWalletCandidate.token_count >= min_appearances,
            SmartWalletCandidate.is_promoted == False,
        )
    ).all()

    for c in candidates:
        # Check if already tracked
        existing = session.get(TrackedWallet, c.wallet_address)
        if existing:
            c.is_promoted = True
            session.add(c)
            continue

        wallet = TrackedWallet(
            address=c.wallet_address,
            label=f"Discovery_{c.token_count}x ({c.token_symbols[:30]})",
            status=WalletStatus.active,
            source="discovery",
        )
        session.add(wallet)
        c.is_promoted = True
        session.add(c)
        promoted += 1

    session.commit()
    logger.info(f"Auto-promoted {promoted} wallets to tracked_wallets")
    return promoted


# ──────────────────────────────────────────────
# Full Discovery Pipeline
# ──────────────────────────────────────────────


async def run_discovery(
    min_gain: float = MIN_GAIN_MULTIPLE,
    lookback_days: int = LOOKBACK_DAYS,
    early_buyer_count: int = DEFAULT_EARLY_BUYER_COUNT,
    auto_promote: bool = False,
) -> dict:
    """
    Run the full smart wallet discovery pipeline.

    1. Find winning tokens from DexScreener
    2. Extract early buyers from each via Helius
    3. Cross-reference to find smart money
    4. Optionally auto-promote to tracked_wallets

    Returns summary dict.
    """
    logger.info(
        f"Starting discovery: min_gain={min_gain}x, "
        f"lookback={lookback_days}d, early_buyers={early_buyer_count}"
    )

    # Step 1: Discover winners
    winners = await discover_winning_tokens(min_gain, lookback_days)
    if not winners:
        logger.warning("No winning tokens found. Try lowering min_gain.")
        return {"winners": 0, "buyers": 0, "candidates": 0, "promoted": 0}

    # Save winners to DB
    with Session(engine) as session:
        for w in winners:
            existing = session.exec(
                select(DiscoveredToken).where(DiscoveredToken.address == w["address"])
            ).first()
            if existing:
                # Update
                existing.current_market_cap = w["market_cap"]
                existing.gain_multiple = w["gain_multiple"]
                session.add(existing)
            else:
                dt = DiscoveredToken(
                    address=w["address"],
                    symbol=w["symbol"],
                    name=w["name"],
                    dex=w["dex"],
                    peak_market_cap=w["market_cap"],
                    current_market_cap=w["market_cap"],
                    launch_market_cap=5000.0,
                    gain_multiple=w["gain_multiple"],
                    pair_created_at=w["pair_created_at"],
                    status=DiscoveryStatus.pending,
                )
                session.add(dt)
        session.commit()
        logger.info(f"Saved {len(winners)} winning tokens to DB")

    # Step 2: Extract early buyers for each winner
    total_buyers = 0
    with Session(engine) as session:
        pending = session.exec(
            select(DiscoveredToken).where(
                DiscoveredToken.status.in_(
                    [DiscoveryStatus.pending, DiscoveryStatus.processing]
                )
            )
        ).all()

        for dt in pending:
            dt.status = DiscoveryStatus.processing
            session.add(dt)
            session.commit()

            try:
                buyers = await extract_early_buyers(dt.address, limit=early_buyer_count)

                for b in buyers:
                    existing_buyer = session.exec(
                        select(EarlyBuyer).where(
                            EarlyBuyer.token_address == dt.address,
                            EarlyBuyer.wallet_address == b["wallet_address"],
                        )
                    ).first()
                    if not existing_buyer:
                        eb = EarlyBuyer(
                            token_id=dt.id,
                            token_address=dt.address,
                            wallet_address=b["wallet_address"],
                            entry_timestamp=b["entry_timestamp"],
                            tx_signature=b["tx_signature"],
                        )
                        session.add(eb)

                dt.early_buyers_found = len(buyers)
                dt.status = DiscoveryStatus.done
                total_buyers += len(buyers)

            except Exception as e:
                logger.error(f"Error processing {dt.symbol}: {e}")
                dt.status = DiscoveryStatus.error

            session.add(dt)
            session.commit()

            # Rate limit: small delay between tokens
            await asyncio.sleep(1.0)

    # Step 3: Cross-reference
    with Session(engine) as session:
        candidates = identify_smart_wallets(session)
        saved = save_smart_wallet_candidates(session, candidates)

        # Step 4: Auto-promote if requested
        promoted = 0
        if auto_promote:
            promoted = auto_promote_candidates(session)

    summary = {
        "winners": len(winners),
        "buyers": total_buyers,
        "candidates": len(candidates),
        "promoted": promoted,
    }
    logger.info(f"Discovery complete: {summary}")
    return summary


async def run_discovery_background():
    """
    Background task that runs discovery on startup and then periodically.
    """
    # Wait a few seconds for the app to fully start
    await asyncio.sleep(5)

    logger.info("Running initial smart wallet discovery...")
    try:
        # First run: use lower threshold to find more tokens
        summary = await run_discovery(
            min_gain=100,  # 100x = 10,000% — cast wider net first time
            lookback_days=LOOKBACK_DAYS,
            early_buyer_count=DEFAULT_EARLY_BUYER_COUNT,
            auto_promote=True,
        )
        logger.info(f"Initial discovery results: {summary}")
    except Exception as e:
        logger.error(f"Initial discovery failed: {e}", exc_info=True)

    # Periodically re-run every 6 hours
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            logger.info("Running periodic smart wallet discovery...")
            summary = await run_discovery(
                min_gain=MIN_GAIN_MULTIPLE,
                auto_promote=True,
            )
            logger.info(f"Periodic discovery results: {summary}")
        except Exception as e:
            logger.error(f"Periodic discovery failed: {e}", exc_info=True)
