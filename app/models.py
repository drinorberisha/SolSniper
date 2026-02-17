from datetime import datetime
from typing import Optional
from sqlmodel import Field, SQLModel
from enum import Enum


class WalletStatus(str, Enum):
    active = "active"
    paused = "paused"


class TokenStatus(str, Enum):
    bonding_curve = "bonding_curve"
    graduated = "graduated"
    rugged = "rugged"


class DiscoveryStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    error = "error"


class TrackedWallet(SQLModel, table=True):
    __tablename__ = "tracked_wallets"

    address: str = Field(primary_key=True)
    label: str
    win_rate: Optional[float] = None
    status: WalletStatus = Field(default=WalletStatus.active)
    source: Optional[str] = None  # "manual", "discovery", "backtester"
    tracked_at: datetime = Field(default_factory=datetime.utcnow)


class Token(SQLModel, table=True):
    __tablename__ = "tokens"

    contract_address: str = Field(primary_key=True)
    symbol: str
    created_at: datetime
    dev_address: str
    market_cap_at_scan: float
    status: TokenStatus = Field(default=TokenStatus.bonding_curve)


class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: Optional[int] = Field(default=None, primary_key=True)
    token_address: str = Field(foreign_key="tokens.contract_address")
    smart_wallet_count: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    confidence_score: int
    is_executed: bool = Field(default=False)


# ── Discovery Tables ──


class DiscoveredToken(SQLModel, table=True):
    """Winning tokens found by the automated discovery engine."""

    __tablename__ = "discovered_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    address: str = Field(index=True, unique=True)
    symbol: str
    name: Optional[str] = None
    dex: Optional[str] = None  # pumpfun, raydium, meteora, etc.
    peak_market_cap: float = 0.0
    current_market_cap: float = 0.0
    launch_market_cap: float = 0.0  # ~$5k for pump.fun
    gain_multiple: float = 0.0  # peak / launch (e.g. 200 = 200x)
    pair_created_at: Optional[datetime] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    status: DiscoveryStatus = Field(default=DiscoveryStatus.pending)
    early_buyers_found: int = 0


class EarlyBuyer(SQLModel, table=True):
    """Early buyers for discovered winning tokens."""

    __tablename__ = "early_buyers"

    id: Optional[int] = Field(default=None, primary_key=True)
    token_id: int = Field(foreign_key="discovered_tokens.id", index=True)
    token_address: str = Field(index=True)
    wallet_address: str = Field(index=True)
    entry_timestamp: Optional[datetime] = None
    tx_signature: Optional[str] = None
    # How many winning tokens this wallet appeared in (denormalized for speed)
    appearances: int = Field(default=1)


class SmartWalletCandidate(SQLModel, table=True):
    """Wallets that appeared as early buyers across multiple winners."""

    __tablename__ = "smart_wallet_candidates"

    id: Optional[int] = Field(default=None, primary_key=True)
    wallet_address: str = Field(index=True, unique=True)
    token_count: int = 0  # How many discovered tokens they were early on
    token_symbols: str = ""  # Comma-separated list of token symbols
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    is_promoted: bool = Field(default=False)  # If added to tracked_wallets
