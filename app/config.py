from pydantic_settings import BaseSettings
from typing import Optional
import re

# Pump.fun Program ID on Solana mainnet
PUMP_FUN_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


# Helius WebSocket URL is derived from the HTTPS RPC URL
def _derive_ws_url(rpc_url: str) -> str:
    """Convert Helius HTTPS URL to WSS URL for WebSocket subscriptions."""
    return rpc_url.replace("https://", "wss://")


def _extract_api_key(rpc_url: str) -> str:
    """Extract the API key from a Helius RPC URL for use with the REST API."""
    match = re.search(r"api-key=([^&]+)", rpc_url)
    return match.group(1) if match else ""


class Settings(BaseSettings):
    DATABASE_URL: str
    SOLANA_RPC_URL: str
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    @property
    def SOLANA_WS_URL(self) -> str:
        return _derive_ws_url(self.SOLANA_RPC_URL)

    @property
    def HELIUS_API_KEY(self) -> str:
        return _extract_api_key(self.SOLANA_RPC_URL)

    @property
    def HELIUS_REST_URL(self) -> str:
        return f"https://api.helius.xyz/v0"

    class Config:
        env_file = ".env"


settings = Settings()
