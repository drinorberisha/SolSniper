# Solana Sniper Stack — Implementation Progress

**Last Updated:** 2026-02-17

---

## API Keys & Services Required

| #   | Key / Service                                | Purpose                                   | Where to Get                        | Status                                         |
| --- | -------------------------------------------- | ----------------------------------------- | ----------------------------------- | ---------------------------------------------- |
| 1   | **Neon Postgres `DATABASE_URL`**             | Database for all tables                   | [neon.tech](https://neon.tech)      | ✅ Configured                                  |
| 2   | **Solana RPC URL (Helius)** `SOLANA_RPC_URL` | Real-time blockchain data, tx fetching    | [helius.dev](https://helius.dev)    | ✅ Configured                                  |
| 3   | **Bitquery API Key**                         | Historical GraphQL queries for backtester | [bitquery.io](https://bitquery.io)  | ❌ Not needed (replaced by Helius+DexScreener) |
| 4   | **Telegram Bot Token** `TELEGRAM_BOT_TOKEN`  | Push signal alerts to phone               | [BotFather](https://t.me/BotFather) | ⏳ Optional                                    |
| 5   | **Telegram Chat ID** `TELEGRAM_CHAT_ID`      | Target chat for alerts                    | Telegram API                        | ⏳ Optional                                    |

---

## Implementation Items

### Phase 2: The Data Pipeline

| #   | Item                            | Description                                                                                         | Status  |
| --- | ------------------------------- | --------------------------------------------------------------------------------------------------- | ------- |
| 1   | **Real Solana RPC listener**    | `listener.py` — Use Helius RPC to listen for Pump.fun `create` events instead of random fake tokens | ✅ Done |
| 2   | **Real `get_token_metadata()`** | `analyzer.py` — Fetch creation time, symbol, dev address, market cap from chain via RPC             | ✅ Done |
| 3   | **Real `get_token_signers()`**  | `analyzer.py` — Use `getSignaturesForAddress` to fetch last 50 txs and extract unique signers       | ✅ Done |

### Phase 3: The Logic & Database

| #   | Item                              | Description                                                         | Status                                  |
| --- | --------------------------------- | ------------------------------------------------------------------- | --------------------------------------- |
| 4   | **Enforce smart money threshold** | Un-comment `count < 2` discard logic in `analyzer.py`               | ✅ Done                                 |
| 5   | **Dev Bundle / Anti-Rug check**   | Detect if dev funded >5 wallets that bought in same block           | ❌ Not started                          |
| 6   | **Narrative keyword boost**       | Regex match token name for trending keywords, +20 confidence        | ❌ Not started                          |
| 7   | **Wallet CRUD router**            | `routers/wallets.py` — dedicated router with delete & status toggle | ✅ Partial (delete + copy + timestamps) |

### Phase 3.5: Price & Status Tracking

| #   | Item                              | Description                                                               | Status  |
| --- | --------------------------------- | ------------------------------------------------------------------------- | ------- |
| 8   | **Price bot real implementation** | `price_bot.py` — periodically update market cap & graduated/rugged status | ✅ Done |
| 9   | **Start price bot in lifespan**   | Wire `price_updater()` into `main.py` startup                             | ✅ Done |

### Phase 4: UI Enhancements

| #   | Item                     | Description                                               | Status         |
| --- | ------------------------ | --------------------------------------------------------- | -------------- |
| 10  | **Signal detail in UI**  | Show token symbol, copyable address, Photon/Trojan link   | ✅ Done        |
| 11  | **System status toggle** | Listening/Paused indicator (currently hardcoded "Active") | ❌ Not started |
| 12  | **Wallet delete button** | UI for removing tracked wallets                           | ✅ Done        |
| 13  | **Error handling**       | Proper duplicate key handling, validation                 | ❌ Not started |

### Phase 5: Telegram Alerts

| #   | Item                       | Description                                | Status         |
| --- | -------------------------- | ------------------------------------------ | -------------- |
| 14  | **Telegram alert sending** | Send signal notifications via Telegram bot | ❌ Not started |

### Phase 6: Backtester ("Time Machine" from Refine.md)

| #   | Item                                        | Description                                        | Status  |
| --- | ------------------------------------------- | -------------------------------------------------- | ------- |
| 15  | **`DiscoveredToken` + `EarlyBuyer` models** | New SQLModel tables for discovery data             | ✅ Done |
| 16  | **`tasks/wallet_discovery.py` engine**      | Automated discovery: DexScreener + Helius pipeline | ✅ Done |
| 17  | **Discovery UI + router**                   | `/discovery/*` endpoints + HTMX templates          | ✅ Done |

---

## Completed Work

| Date       | Items        | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ---------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| (initial)  | Foundation   | FastAPI app, models, database, HTMX dashboard, stub listener/analyzer/price_bot                                                                                                                                                                                                                                                                                                                                                             |
| 2026-02-16 | Items 1-3    | Real Solana RPC integration: WebSocket listener for Pump.fun create events (with polling fallback), real `get_token_metadata()` via RPC + Helius DAS, real `get_token_signers()` via `getSignaturesForAddress`. Smart money threshold (`count < 2` discard) now enforced. Updated `requirements.txt` with `solana`, `solders`, `websockets`, `aiohttp`. Added `PUMP_FUN_PROGRAM_ID` and `SOLANA_WS_URL` to config.                          |
| 2026-02-16 | Items 4,7,12 | Enforced smart-money threshold. Wallet delete button added (dashboard router + template).                                                                                                                                                                                                                                                                                                                                                   |
| 2026-02-16 | Items 15-17  | **Smart Wallet Discovery Engine**: Automated pipeline that (1) finds winning tokens (≥100x) from DexScreener, (2) extracts earliest buyers via Helius Enhanced API, (3) cross-references to identify wallets appearing in 2+ winners. New files: `tasks/wallet_discovery.py`, `routers/discovery.py`, 3 template partials. Runs automatically on startup + every 6 hours. First run found **9 winners** and **10 smart wallet candidates**. |
| 2026-02-17 | Items 7,12+  | DB migration system in `main.py`. Fixed missing `source` column crash. Added `tracked_at` timestamp to `TrackedWallet`. Scrollable wallet container (fixed height). Click-to-copy with "Copied!" feedback on wallet addresses & candidate addresses. Source/timestamp row on each wallet card. `discovered_at` column on winning tokens table. Custom scrollbar styling.                                                                    |
| 2026-02-17 | Items 8-10   | **Price Bot**: Real implementation using DexScreener API — updates market caps every 60s, detects graduated (Raydium/Meteora, >$50k MC) and rugged (<$500 MC) tokens. Wired into `main.py` lifespan. **Signal UI overhaul**: Enriched signals now show token symbol, status badge (bonding_curve/graduated/rugged), click-to-copy address, live market cap, confidence score color coding, and Photon + DexScreener quick-open links.       |
