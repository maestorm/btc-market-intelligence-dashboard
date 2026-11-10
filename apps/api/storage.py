from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

DB_PATH = Path(__file__).resolve().parent / "data" / "market.duckdb"


def init_storage() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                observed_at TIMESTAMPTZ NOT NULL,
                btc_usd DOUBLE,
                btc_usd_source VARCHAR,
                dvol DOUBLE,
                realized_vol_30d_pct DOUBLE,
                funding_rate_pct DOUBLE,
                open_interest_btc DOUBLE,
                open_interest_usd DOUBLE,
                fear_greed_value DOUBLE,
                fear_greed_classification VARCHAR
            )
        """)
    finally:
        con.close()


def save_market_snapshot(data: dict[str, Any]) -> None:
    fear_greed = data.get("fearGreed") or {}

    observed_at = datetime.fromisoformat(
        data["updatedAt"].replace("Z", "+00:00")
    )

    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute(
            """
            INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observed_at,
                data.get("btcUsd"),
                data.get("btcUsdSource"),
                data.get("dvol"),
                data.get("realizedVol30dPct"),
                data.get("fundingRatePct"),
                data.get("openInterestBtc"),
                data.get("openInterestUsd"),
                fear_greed.get("value"),
                fear_greed.get("classification"),
            ],
        )
    finally:
        con.close()
