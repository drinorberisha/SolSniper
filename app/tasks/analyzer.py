"""
analyzer.py — Core analysis logic for the Solana Sniper Stack.

Implements the scan_new_token() algorithm:
  1. Time Check — discard tokens older than 10 minutes
  2. Smart Money Cross-Reference — require >= 2 tracked wallet matches
  3. (Future) Dev Forensics / Anti-Rug check
  4. (Future) Narrative keyword boost
  5. Signal Generation — save Token + Signal to DB
"""

from sqlmodel import Session, select
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

import httpx

from app.config import settings
from app.models import Signal, Token, TrackedWallet

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Helius Enhanced API Helpers
# ──────────────────────────────────────────────


def _helius_tx_url(address: str, limit: int = 50) -> str:
    """Build the Helius Enhanced Transactions API URL."""
    return (
        f"{settings.HELIUS_REST_URL}/addresses/{address}/transactions"
        f"?api-key={settings.HELIUS_API_KEY}&limit={limit}"
    )


async def _fetch_enhanced_transactions(
    client: httpx.AsyncClient, token_address: str, limit: int = 100
) -> list[dict]:
    """
    Fetch enriched transactions for a token via Helius Enhanced Transactions API.
    Returns a list of parsed transaction dicts with keys like:
      type, source, feePayer, signature, timestamp, tokenTransfers, etc.
    """
    url = _helius_tx_url(token_address, limit=limit)
    resp = await client.get(url)
    resp.raise_for_status()
    data = resp.json()

    if isinstance(data, list):
        return data

    # Error response
    logger.warning(f"Helius API error for {token_address}: {data}")
    return []


async def get_token_metadata(token_address: str) -> Optional[dict]:
    """
    Fetch real token metadata using the Helius Enhanced Transactions API + DAS.

    Returns dict with keys: symbol, created_at, dev_address, market_cap
    or None if the token can't be fetched / parsed.

    Strategy:
    - Enhanced Transactions API → get oldest tx for creation time + dev (feePayer)
    - Helius DAS getAsset → symbol/name
    - Market cap set to 0 (price_bot updates later)
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Get transactions to find creation time + dev wallet
            txs = await _fetch_enhanced_transactions(client, token_address, limit=100)

            created_at = datetime.now(timezone.utc)
            dev_address = "unknown"

            if txs:
                # Oldest transaction is last in the list (sorted newest-first)
                oldest_tx = txs[-1]
                ts = oldest_tx.get("timestamp")
                if ts:
                    created_at = datetime.fromtimestamp(ts, tz=timezone.utc)

                # Dev is the feePayer of the oldest transaction
                dev_address = oldest_tx.get("feePayer", "unknown")

                # If there are many txs (>= limit), we might not have the true oldest.
                # Also try to find the first SWAP with source PUMP_FUN or PUMP_AMM
                for tx in reversed(txs):
                    src = tx.get("source", "")
                    if src in ("PUMP_FUN", "PUMP_AMM", "PUMP"):
                        dev_address = tx.get("feePayer", dev_address)
                        ts2 = tx.get("timestamp")
                        if ts2:
                            created_at = datetime.fromtimestamp(ts2, tz=timezone.utc)
                        break

            # Step 2: Get token symbol via Helius DAS (getAsset)
            symbol = token_address[:6]  # fallback
            try:
                das_resp = await client.post(
                    settings.SOLANA_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "getAsset",
                        "params": {"id": token_address},
                    },
                )
                das_data = das_resp.json()
                das_result = das_data.get("result", {})
                content = das_result.get("content", {})
                metadata = content.get("metadata", {})
                symbol = metadata.get("symbol", symbol)
            except Exception:
                logger.debug(
                    f"DAS getAsset failed for {token_address}, using fallback symbol"
                )

            # Step 3: Market cap (set 0, price_bot updates later)
            market_cap = 0.0

            return {
                "symbol": symbol,
                "created_at": created_at,
                "dev_address": dev_address,
                "market_cap": market_cap,
            }

    except httpx.TimeoutException:
        logger.error(f"Timeout fetching metadata for {token_address}")
        return None
    except Exception as e:
        logger.error(f"Error fetching metadata for {token_address}: {e}", exc_info=True)
        return None


async def get_token_signers(token_address: str, limit: int = 50) -> list[str]:
    """
    Fetch recent transactions for a token via Helius Enhanced Transactions API
    and extract unique fee-payer (signer) addresses.

    Focuses on SWAP transactions (buys/sells) since those represent
    actual wallet interactions with the token.

    Returns a list of unique wallet addresses.
    """
    signers: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            txs = await _fetch_enhanced_transactions(client, token_address, limit=limit)

            if not txs:
                logger.debug(f"No transactions found for {token_address}")
                return []

            for tx in txs:
                fee_payer = tx.get("feePayer")
                if not fee_payer:
                    continue

                # Focus on SWAP transactions (actual buys/sells)
                tx_type = tx.get("type", "")
                if tx_type == "SWAP":
                    signers.add(fee_payer)
                # Also include unknown txs from Pump.fun sources (some swaps
                # are categorized as UNKNOWN with source PUMP_AMM)
                elif tx.get("source", "") in ("PUMP_FUN", "PUMP_AMM", "PUMP"):
                    signers.add(fee_payer)

    except httpx.TimeoutException:
        logger.error(f"Timeout fetching signers for {token_address}")
    except Exception as e:
        logger.error(f"Error fetching signers for {token_address}: {e}", exc_info=True)

    result = list(signers)
    logger.info(f"Found {len(result)} unique signers for {token_address}")
    return result


# ──────────────────────────────────────────────
# Main Scanner
# ──────────────────────────────────────────────


async def scan_new_token(token_address: str, session: Session):
    """
    Full analysis pipeline for a newly discovered token.

    1. Deduplication check
    2. Fetch metadata from chain (time check: discard if > 10 min old)
    3. Fetch signers & cross-reference with tracked wallets (discard if < 2 matches)
    4. Save Token + Signal to DB
    """
    logger.info(f"Scanning token: {token_address}")

    # ── Deduplication ──
    if session.get(Token, token_address):
        logger.debug(f"Token {token_address} already scanned, skipping.")
        return

    # ── 1. Fetch Metadata ──
    meta = await get_token_metadata(token_address)
    if meta is None:
        logger.warning(f"Could not fetch metadata for {token_address}, skipping.")
        return

    # ── Time Check ──
    now = datetime.now(timezone.utc)
    created = meta["created_at"]
    # Ensure created_at is timezone-aware
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = now - created

    if age > timedelta(minutes=10):
        logger.info(f"Token {token_address} too old ({age}), discarding.")
        return

    # ── 2. Get Signers & Cross-Reference with Smart Money ──
    signers = await get_token_signers(token_address)

    stmt = select(TrackedWallet).where(
        TrackedWallet.address.in_(signers),
        TrackedWallet.status == "active",
    )
    matches = session.exec(stmt).all()
    smart_wallet_count = len(matches)

    if smart_wallet_count < 2:
        logger.info(
            f"Token {token_address}: only {smart_wallet_count} smart wallet(s), "
            f"need >= 2. Discarding."
        )
        return

    # ── 3. TODO: Dev Forensics / Anti-Rug Check (Item #5) ──
    # ── 4. TODO: Narrative Keyword Boost (Item #6) ──

    # ── 5. Confidence Score ──
    confidence = 50 + (smart_wallet_count * 10)
    confidence = min(confidence, 100)

    # ── 6. Save Token + Signal ──
    token = Token(
        contract_address=token_address,
        symbol=meta["symbol"],
        created_at=meta["created_at"],
        dev_address=meta["dev_address"],
        market_cap_at_scan=meta["market_cap"],
    )
    session.add(token)

    signal = Signal(
        token_address=token_address,
        smart_wallet_count=smart_wallet_count,
        confidence_score=confidence,
    )
    session.add(signal)
    session.commit()

    matched_labels = [m.label for m in matches]
    logger.info(
        f"✅ SIGNAL GENERATED for {meta['symbol']} ({token_address[:12]}...) | "
        f"Score: {confidence} | Smart wallets: {matched_labels}"
    )
