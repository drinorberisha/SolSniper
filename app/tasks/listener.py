"""
listener.py — Listens for new Pump.fun token creation events on Solana mainnet.

Uses Helius WebSocket `logsSubscribe` to watch the Pump.fun program for
`create` instruction logs, then hands discovered token addresses to the analyzer.

Fallback: If WebSocket connection fails, falls back to polling `getSignaturesForAddress`
on the Pump.fun program ID.
"""

import asyncio
import json
import logging
from typing import Optional

import httpx
import websockets

from sqlmodel import Session

from app.config import settings, PUMP_FUN_PROGRAM_ID
from app.database import engine
from app.tasks.analyzer import scan_new_token

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def _extract_token_address_from_logs(log_messages: list[str]) -> Optional[str]:
    """
    Parse Solana transaction logs from the Pump.fun program to find
    the newly created token mint address.

    Pump.fun logs a line like:
        "Program log: Create: <MINT_ADDRESS>"
    or includes the mint in the instruction data.
    We also look for "InitializeMint" inner instructions.
    """
    for msg in log_messages:
        # Pump.fun create logs typically contain the mint address
        if "Create" in msg or "create" in msg:
            # Try to extract a base58 address (32-44 chars of alphanumeric)
            parts = msg.split()
            for part in parts:
                # Solana addresses are 32-44 base58 characters
                cleaned = part.strip(",.;:'\"()")
                if len(cleaned) >= 32 and len(cleaned) <= 44 and cleaned.isalnum():
                    return cleaned
    return None


async def _extract_token_from_tx(tx_signature: str) -> Optional[str]:
    """
    Fetch a transaction by signature and extract the created token mint address.
    Used when logsSubscribe doesn't give us the mint directly.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        tx_signature,
                        {
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                },
            )
            data = resp.json()
            result = data.get("result")
            if not result:
                return None

            # Walk through inner instructions looking for initializeMint
            meta = result.get("meta", {})
            inner_instructions = meta.get("innerInstructions", [])
            for inner_set in inner_instructions:
                for ix in inner_set.get("instructions", []):
                    parsed = ix.get("parsed", {})
                    if parsed.get("type") == "initializeMint":
                        info = parsed.get("info", {})
                        mint = info.get("mint")
                        if mint:
                            return mint

            # Also check top-level instructions
            tx_msg = result.get("transaction", {}).get("message", {})
            for ix in tx_msg.get("instructions", []):
                parsed = ix.get("parsed", {})
                if parsed.get("type") == "initializeMint":
                    info = parsed.get("info", {})
                    mint = info.get("mint")
                    if mint:
                        return mint

            # Fallback: check account keys for newly created token accounts
            # The second account in Pump.fun create ix is usually the mint
            account_keys = tx_msg.get("accountKeys", [])
            if len(account_keys) >= 3:
                # The mint is typically the 2nd key (index 1) in a Pump.fun create
                candidate = account_keys[1]
                if isinstance(candidate, dict):
                    candidate = candidate.get("pubkey", "")
                if candidate and candidate != PUMP_FUN_PROGRAM_ID:
                    return candidate

    except Exception as e:
        logger.error(f"Error fetching tx {tx_signature}: {e}")
    return None


# ──────────────────────────────────────────────
# WebSocket Listener (Primary)
# ──────────────────────────────────────────────


async def _websocket_listener():
    """
    Subscribe to Pump.fun program logs via WebSocket.
    When a `create` log is detected, extract the token address and scan it.
    """
    ws_url = settings.SOLANA_WS_URL
    logger.info(f"Connecting to Solana WebSocket: {ws_url[:50]}...")

    subscribe_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "logsSubscribe",
            "params": [
                {"mentions": [PUMP_FUN_PROGRAM_ID]},
                {"commitment": "confirmed"},
            ],
        }
    )

    async with websockets.connect(ws_url, ping_interval=30, ping_timeout=60) as ws:
        await ws.send(subscribe_msg)

        # Read subscription confirmation
        confirmation = await ws.recv()
        conf_data = json.loads(confirmation)
        sub_id = conf_data.get("result")
        logger.info(f"Subscribed to Pump.fun logs. Subscription ID: {sub_id}")

        async for message in ws:
            try:
                data = json.loads(message)
                params = data.get("params", {})
                result = params.get("result", {})
                value = result.get("value", {})

                logs = value.get("logs", [])
                signature = value.get("signature", "")

                if not logs:
                    continue

                # Check if this is a token creation event
                is_create = any(
                    "Create" in log or "create" in log or "InitializeMint" in log
                    for log in logs
                )
                if not is_create:
                    continue

                # Try to get the token address from logs first
                token_address = _extract_token_address_from_logs(logs)

                # If not found in logs, fetch the full transaction
                if not token_address and signature:
                    logger.debug(f"Fetching tx {signature} to extract mint...")
                    token_address = await _extract_token_from_tx(signature)

                if token_address:
                    logger.info(f"🔫 New Pump.fun token detected: {token_address}")
                    with Session(engine) as session:
                        await scan_new_token(token_address, session)
                else:
                    logger.debug(
                        f"Create event without extractable mint. Sig: {signature[:20]}..."
                    )

            except json.JSONDecodeError:
                logger.warning("Non-JSON message from WebSocket")
            except Exception as e:
                logger.error(f"Error processing WS message: {e}", exc_info=True)


# ──────────────────────────────────────────────
# Polling Fallback (if WebSocket fails)
# ──────────────────────────────────────────────


async def _polling_listener():
    """
    Fallback: Poll `getSignaturesForAddress` on the Pump.fun program
    to discover new transactions every ~5 seconds.
    """
    logger.info("Starting Pump.fun polling listener (fallback mode)...")
    seen_signatures: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                resp = await client.post(
                    settings.SOLANA_RPC_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [
                            PUMP_FUN_PROGRAM_ID,
                            {"limit": 20, "commitment": "confirmed"},
                        ],
                    },
                )
                data = resp.json()
                signatures = data.get("result", [])

                for sig_info in signatures:
                    sig = sig_info.get("signature", "")
                    if sig in seen_signatures:
                        continue
                    seen_signatures.add(sig)

                    # Check if it errored
                    if sig_info.get("err") is not None:
                        continue

                    # Fetch the tx to see if it's a create
                    token_address = await _extract_token_from_tx(sig)
                    if token_address:
                        logger.info(f"🔫 New Pump.fun token (poll): {token_address}")
                        with Session(engine) as session:
                            await scan_new_token(token_address, session)

                # Cap memory of seen sigs
                if len(seen_signatures) > 5000:
                    seen_signatures = set(list(seen_signatures)[-2000:])

            except Exception as e:
                logger.error(f"Polling error: {e}")

            await asyncio.sleep(5)


# ──────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────


async def listen_for_new_tokens():
    """
    Primary listener entry point. Tries WebSocket first, falls back to polling.
    Auto-reconnects on failure.
    """
    logger.info("Starting Solana Listener for Pump.fun tokens...")

    while True:
        try:
            await _websocket_listener()
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket connection closed: {e}. Reconnecting in 5s...")
        except websockets.exceptions.InvalidURI:
            logger.error("Invalid WebSocket URI. Falling back to polling mode.")
            await _polling_listener()
            return  # polling_listener has its own loop
        except ConnectionRefusedError:
            logger.warning("WebSocket connection refused. Falling back to polling.")
            await _polling_listener()
            return
        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in 5s...", exc_info=True)

        await asyncio.sleep(5)
