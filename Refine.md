The strategy shift is excellent. Instead of "simulating" forward, you are now building a "Time Machine" to scientifically validate your inputs. You are moving from hoping you find smart money to proving who they are based on forensic evidence.

Here is the refine.md content. It is architected as a Calibration Module that sits alongside your existing app.
Refined Strategy: The "Time Machine" Calibration

Objective: Mathematically identify "Smart Wallets" by backtesting the last 30 days of market data. Goal: Produce a validated list of 20-50 wallet addresses that have proven they entered >20,000% runners early (Day 0) multiple times.
1. The Workflow (30-Day Forensic Loop)

We will not guess. We will scrape, index, and query.
Step A: The "God Candle" Hunter (Daily Indexing)

For each day (D−1​ to D−30​):

    Query: Identify the top 1-3 tokens that hit >20,000% gains (200x) from their launch price.

    Filter:

        Start Market Cap: < $10k (Pump.fun / Fair Launch).

        Peak Market Cap: > $1M (The "God Candle").

        Timeframe: Achieved within 24-48 hours of launch.

Step B: The "First 100" Extraction

For each identified "Winner" token:

    Fetch Transaction History: Retrieve the first 1,000 transactions starting from the "Mint" or "Bonding Curve" creation block.

    Sort & Filter:

        Sort by block_time (Ascending).

        Identify the First 100 Unique Wallets that bought.

        Crucial: Exclude the Dev wallet and the Liquidity Provider address.

Step C: The Cross-Reference (The "Alpha" Filter)

    Aggregation: You now have ~30 days × ~2 tokens × 100 wallets = ~6,000 wallet addresses.

    The Overlap Check:

        Query: SELECT address, count(distinct token_id) FROM early_buyers GROUP BY address HAVING count > 1

        Logic: If a wallet appears in the "First 100" of 2 or more unrelated God Candles in the last month, it is NOT luck. It is a Sniper, an Insider, or a high-quality Alpha Group member.

    Result: This subset (likely 20-50 wallets) is your "Golden List".

2. Technical Implementation Updates
New Dependencies

    Bitquery (or BirdEye) API: Required for historical "First Buyer" queries. Standard RPCs are too slow for fetching old blocks efficiently.

        Why Bitquery? It allows GraphQL queries like "Get first 100 trades for Token X sorted by time".

Database Schema Changes (models.py)

We need new tables to store this forensic data.
Python

class HistoricalToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str
    address: str
    date_of_run: datetime
    peak_market_cap: float
    # Metadata to learn patterns later
    time_to_200x: int # in minutes

class HistoricalBuyer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    token_id: int = Field(foreign_key="historicaltoken.id")
    wallet_address: str
    entry_time: datetime
    entry_price: float
    # Did they hold? (Optional advanced metric)
    sold_time: Optional[datetime] = None

New Module: tasks/backtester.py

This script will run once (or weekly) to populate your database.
Python

# Pseudocode logic for backtester.py

async def run_30_day_forensics():
    # 1. Manual Input or API fetch of the "Winners" list for last 30 days
    # (You might compile this list manually from DexScreener to start)
    winners = ["TokenA_Address", "TokenB_Address", ...] 

    for token in winners:
        # 2. Get the first 100 buyers via Bitquery/Solscan
        buyers = await bitquery_api.get_first_buyers(token, limit=100)
        
        # 3. Store in DB
        for buyer in buyers:
             db.add(HistoricalBuyer(wallet_address=buyer.address, ...))
    
    # 4. Find the Snipers
    query = """
    SELECT wallet_address 
    FROM historicalbuyer 
    GROUP BY wallet_address 
    HAVING count(*) >= 2
    """
    smart_wallets = db.exec(query).all()
    
    # 5. Insert into your MAIN TrackedWallet table
    for wallet in smart_wallets:
        db.add(TrackedWallet(address=wallet, label="Backtested_Sniper"))

3. Data Sources (The "How")
Finding the "Winners" (Step A)

    Source: DexScreener (Free).

    Method: Go to "Gainers" -> Filter by "Solana" -> Timeframe "Monthly" or check daily historical snapshots if available.

    Manual vs Auto: For the first run, manually picking the top 2 cleanest charts per day (60 tokens total) is better than automating trash detection.

Finding the "First Buyers" (Step B)

    Source: Bitquery GraphQL (Best) or Solscan API (Good but rate-limited).

    Bitquery Query Example:
    GraphQL

    {
      Solana {
        DEXTrades(
          options: {limit: 100, asc: "Block.Time"}
          where: {Trade: {Buy: {Currency: {MintAddress: {is: "YOUR_TOKEN_ADDRESS"}}}}}
        ) {
          Transaction { Signer }
          Block { Time }
        }
      }
    }

4. Execution Plan

    Manual Compilation (Day 1): Create a spreadsheet/list of 30-60 token addresses that hit >200x in the last month.

    Scripting (Day 2): Write tasks/backtester.py to iterate through that list and fetch buyers using Bitquery.

    Analysis (Day 2): Run the SQL query to identify the overlapping wallets.

    Integration (Day 3): Feed these result wallets into your existing listener.py to start the real-time sniper.