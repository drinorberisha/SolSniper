# Keys Needed

You need to retrieve the following **2 keys** and replace the values in your `.env` file:

1.  **`DATABASE_URL`**
    *   **What is it?**: Your Neon Postgres Connection String.
    *   **Where to find it**: Go to your Neon Dashboard -> Select Project -> "Dashboard" or "Connection Details". Copy the "Connection String" (starts with `postgresql://`).

2.  **`SOLANA_RPC_URL`**
    *   **What is it?**: Your Solana RPC API URL.
    *   **Where to find it**: Go to your Helius Dashboard (or other provider) -> "RPCs" -> "Mainnet" -> Copy the HTTPS URL (usually contains an API key).

**Next Step:**
Open `.env`, paste these values, save, and run `uvicorn app.main:app --reload` again.
