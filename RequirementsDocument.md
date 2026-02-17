# Project: Solana Sniper Stack (The "Genesis" Engine)

**Objective:** Automate the detection of high-potential Solana tokens at the "Bonding Curve" stage (~$5k Market Cap) that exhibit "Smart Money" accumulation, targeting a run to $5M+.
**Stack:** Python (FastAPI), HTMX (Frontend), Neon (Postgres), AsyncIO.

---

## 1. System Architecture

The system follows an **Event-Driven Architecture**. The backend continuously ingests blockchain data, filters it through a logic engine, stores valid signals in Neon, and pushes updates to the HTMX frontend via polling or WebSockets.

### High-Level Flow
1.  **Ingestor:** Listens to Solana RPC (Pump.fun `create` events).
2.  **Processor:**
    * Checks if the token is "fresh" (<5 mins old).
    * Queries recent buyers against the "Smart Money" whitelist.
    * Performs "Anti-Rug" checks (Dev bundling).
3.  **Database (Neon):** Stores `Tokens`, `Signals`, and `TrackedWallets`.
4.  **API (FastAPI):** Serves data to the frontend.
5.  **UI (HTMX):** Real-time dashboard for monitoring signals and managing the wallet watchlist.

---

## 2. Database Schema (Neon / Postgres)

Use `SQLAlchemy` or `SQLModel` for ORM.

### Table: `tracked_wallets` (The "Alpha" List)
* `address` (Primary Key, String): The wallet address of a known winner.
* `label` (String): e.g., "KOL_Ansem", "Smart_Whale_01".
* `win_rate` (Float): Percentage of profitable trades (optional, from GMGN.ai).
* `status` (Enum): `active`, `paused`.

### Table: `tokens` (The Scanned Assets)
* `contract_address` (Primary Key, String).
* `symbol` (String).
* `created_at` (Timestamp): Time of block creation.
* `dev_address` (String): The creator's wallet.
* `market_cap_at_scan` (Float).
* `status` (Enum): `bonding_curve`, `graduated`, `rugged`.

### Table: `signals` (The "Buy" Alerts)
* `id` (Primary Key, Auto).
* `token_address` (ForeignKey -> tokens.contract_address).
* `smart_wallet_count` (Int): How many tracked wallets bought this.
* `timestamp` (Timestamp).
* `confidence_score` (Int): Calculated score (0-100).
* `is_executed` (Boolean): If auto-buy was triggered (future feature).

---

## 3. Backend Modules (FastAPI)

Structure your `app/` directory as follows:

```text
/app
├── main.py            # App entry point, HTMX template rendering
├── config.py          # Env vars (RPC_URL, DB_URL)
├── database.py        # Neon connection & Session dependency
├── models.py          # SQLModel tables
├── tasks/
│   ├── listener.py    # Async loop listening to Solana RPC
│   ├── analyzer.py    # Logic for "Smart Money" & "Anti-Rug"
│   └── price_bot.py   # Periodically updates MC of signaled tokens
├── routers/
│   ├── dashboard.py   # HTMX endpoints for the UI
│   └── wallets.py     # CRUD for tracked_wallets
└── templates/         # Jinja2 templates
    ├── index.html
    └── partials/      # HTMX partials (rows, cards)
```

## 4. The Core Logic (The "Secret Sauce")

This logic resides in `tasks/analyzer.py`. This is the Python implementation of the strategy discussed.
Algorithm: `scan_new_token(token_address)`

### Time Check
* Fetch creation_time from RPC.
* IF `age > 10 minutes`: DISCARD (Too late for $5k entry).

### Smart Money Cross-Reference
* Fetch last 50 transactions for `token_address` (using `getSignaturesForAddress`).
* Extract unique signer addresses.
* Query DB: `SELECT * FROM tracked_wallets WHERE address IN (signer_addresses)`.
* IF `count(matches) < 2`: DISCARD (Not enough signal).

### Dev Forensics (Anti-Rug)
* Identify `dev_address` (first signer).
* **Bundle Check:** Did the Dev wallet fund >5 other wallets that bought in the same block?
* IF `True`: DISCARD (Cabal Bundle / Scam).

### Graduation Potential (Optional Narrative Check)
* Regex match `token_name` against trending keywords (e.g., "AI", "Agent", "Quant").
* IF match: Add +20 to `confidence_score`.

### Signal Generation
* Create Signal entry in DB.
* Trigger HTMX update event.

## 5. Frontend (HTMX + Jinja2)

Keep it lightweight. No React/Vue needed.
Page: `index.html` (The Dashboard)

### Header
* System Status (Listening/Paused).

### Section 1: Live Signals (The "Feed")
* Use `hx-get="/signals/latest"` with `hx-trigger="every 2s"` to poll for new hits.
* Display: Token Name, Contract Address (Copyable), # of Smart Wallets, Time Found.
* Action Button: Link to Photon/Trojan for 1-click buy.

### Section 2: Tracked Wallets Manager
* Form to input a new "Smart Money" address.
* List of currently tracked wallets with Delete button.

---

## 6. Implementation Roadmap

### Phase 1: Foundation (Hours 1-2)
* Initialize FastAPI project with uv or poetry.
* Set up Neon Postgres instance.
* Define SQLModel schemas and run migrations.
* Create the basic HTMX dashboard (static data).

### Phase 2: The Data Pipeline (Hours 3-5)
* Integrate `solana-py` or `solders`.
* Write the `listener.py` background task to print new token addresses from Pump.fun program ID.
* Implement `get_token_holders` function to fetch buyers.

### Phase 3: The Logic & Database (Hours 6-8)
* Connect the Listener to the Analyzer.
* Implement the "Cross-Reference" logic against your DB of wallets.
* Save valid hits to the signals table.
* Wire up the HTMX polling to fetch from the real DB.

### Phase 4: Refinement (Hours 9+)
* Add the "Dev Bundle" check (crucial for filtering scams).
* Deploy to a VPS (Hetzner/DigitalOcean) or Railway for 24/7 uptime.

---

## 7. Configuration Variables (.env)

```env
DATABASE_URL="postgresql://user:pass@ep-xyz.neon.tech/neondb"
SOLANA_RPC_URL="https://mainnet.helius-rpc.com/?api-key=..."
# Optional: Telegram Bot Token for mobile alerts
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
```