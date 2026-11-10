import asyncio
import logging
import math
import re
import statistics
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query

from storage import init_storage, save_market_snapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public/free data sources
# ---------------------------------------------------------------------------
DERIBIT_API = "https://www.deribit.com/api/v2"
BINANCE_FUTURES_API = "https://fapi.binance.com"
ALTERNATIVE_API = "https://api.alternative.me"

SYMBOL_TERMS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "ether", "eth"],
    "XRP": ["xrp", "ripple"],
    "SOL": ["solana", "sol"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
}

RSS_FEEDS = {
    "news": "https://www.investing.com/rss/news_301.rss",
    "analysis": "https://www.investing.com/rss/302.rss",
}

CACHE_TTL_SECONDS = 30
_market_cache: dict[str, Any] = {"expires_at": 0.0, "value": None}
_market_cache_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(init_storage)
    yield


app = FastAPI(lifespan=lifespan)


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any | None:
    """Fetch JSON without allowing a failure of one provider to break the API."""
    try:
        response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "API HTTP error: url=%s status=%s body=%s",
            url,
            exc.response.status_code,
            exc.response.text[:250],
        )
    except (httpx.RequestError, ValueError) as exc:
        logger.warning("API request failed: url=%s error=%r", url, exc)
    return None


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def utc_iso_from_milliseconds(value: Any) -> str | None:
    timestamp = as_float(value)
    if timestamp is None:
        return None
    return (
        datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("-USD", "").replace("/USD", "")
    return "all" if normalized in {"", "ALL"} else normalized


# ---------------------------------------------------------------------------
# News feed - the original RSS functionality is retained
# ---------------------------------------------------------------------------
async def fetch_rss_items(client: httpx.AsyncClient) -> list[Any]:
    items_by_url: dict[str, Any] = {}

    for feed_name, url in RSS_FEEDS.items():
        try:
            response = await client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "xml")

            for item in soup.find_all("item"):
                link_tag = item.find("link")
                link = link_tag.get_text(strip=True) if link_tag else ""
                if link:
                    items_by_url[link] = item
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Could not load RSS feed %s (%s): %r", feed_name, url, exc)

    return list(items_by_url.values())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/news")
async def get_news(
    symbol: str = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=50),
):
    normalized_symbol = normalize_symbol(symbol)
    if normalized_symbol != "all" and normalized_symbol not in SYMBOL_TERMS:
        supported = ", ".join(["all", *SYMBOL_TERMS.keys()])
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported symbol. Use one of: {supported}",
        )

    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={"User-Agent": "CryptoNewsReader/1.0"},
    ) as client:
        items = await fetch_rss_items(client)

    if not items:
        raise HTTPException(
            status_code=502,
            detail="RSS feed contains no articles or has an unexpected format.",
        )

    results = []
    for item in items:
        title_tag = item.find("title")
        link_tag = item.find("link")
        description_tag = item.find("description")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        link = link_tag.get_text(strip=True) if link_tag else ""

        if not title or not link:
            continue

        raw_description = description_tag.get_text() if description_tag else ""
        summary = BeautifulSoup(raw_description, "html.parser").get_text(" ", strip=True)

        if normalized_symbol != "all":
            search_text = f"{title} {summary}".lower()
            if not any(
                re.search(rf"\b{re.escape(term)}\b", search_text)
                for term in SYMBOL_TERMS[normalized_symbol]
            ):
                continue

        date_tag = item.find(
            lambda tag: tag.name
            and tag.name.rsplit(":", 1)[-1].lower()
            in {"pubdate", "published", "updated", "date"}
        )
        raw_date = date_tag.get_text(strip=True) if date_tag else ""
        published_at = None

        if raw_date:
            parsed_date = None
            try:
                parsed_date = parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                try:
                    parsed_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning("Could not parse RSS publication date: %r", raw_date)

            if parsed_date is not None:
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                published_at = (
                    parsed_date.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )

        results.append(
            {
                "title": title,
                "source": "Investing.com",
                "url": link,
                "publishedAt": published_at,
                "summary": summary or None,
            }
        )
        if len(results) >= limit:
            break

    return results


# ---------------------------------------------------------------------------
# Candle parsing and indicator calculations
# ---------------------------------------------------------------------------
def parse_klines(
    rows: Any,
    *,
    drop_incomplete: bool = False,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []

    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    candles: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, list) or len(row) < 12:
            continue

        open_time = as_float(row[0])
        open_price = as_float(row[1])
        high = as_float(row[2])
        low = as_float(row[3])
        close = as_float(row[4])
        volume = as_float(row[5])
        close_time = as_float(row[6])
        quote_volume = as_float(row[7])
        trades = as_float(row[8])
        taker_buy_volume = as_float(row[9])
        taker_buy_quote_volume = as_float(row[10])

        required = [open_time, open_price, high, low, close, close_time]
        if any(value is None for value in required):
            continue
        if min(open_price, high, low, close) <= 0:
            continue
        if drop_incomplete and close_time > now_ms:
            continue

        candles.append(
            {
                "openTime": int(open_time),
                "closeTime": int(close_time),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "quoteVolume": quote_volume,
                "trades": int(trades) if trades is not None else None,
                "takerBuyVolume": taker_buy_volume,
                "takerBuyQuoteVolume": taker_buy_quote_volume,
            }
        )

    return candles


def ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None

    current = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        current = (value - current) * multiplier + current
    return current


def ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []

    current = sum(values[:period]) / period
    result = [current]
    multiplier = 2 / (period + 1)
    for value in values[period:]:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def calculate_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None

    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    initial = changes[:period]
    average_gain = sum(change for change in initial if change > 0) / period
    average_loss = -sum(change for change in initial if change < 0) / period

    # Wilder smoothing, as used by the conventional RSI calculation.
    for change in changes[period:]:
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None

    true_ranges = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    return atr


def calculate_adx(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """A Wilder-style ADX estimate; returns None when the sample is too short."""
    if len(candles) < (period * 2 + 2):
        return None

    true_ranges: list[float] = []
    plus_moves: list[float] = []
    minus_moves: list[float] = []

    for previous, current in zip(candles, candles[1:]):
        up_move = current["high"] - previous["high"]
        down_move = previous["low"] - current["low"]
        plus_moves.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_moves.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )

    smoothed_tr = sum(true_ranges[:period])
    smoothed_plus = sum(plus_moves[:period])
    smoothed_minus = sum(minus_moves[:period])
    dx_values: list[float] = []

    for index in range(period, len(true_ranges)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[index]
        smoothed_plus = smoothed_plus - (smoothed_plus / period) + plus_moves[index]
        smoothed_minus = smoothed_minus - (smoothed_minus / period) + minus_moves[index]

        if smoothed_tr <= 0:
            continue
        plus_di = 100 * smoothed_plus / smoothed_tr
        minus_di = 100 * smoothed_minus / smoothed_tr
        denominator = plus_di + minus_di
        dx_values.append(
            100 * abs(plus_di - minus_di) / denominator if denominator > 0 else 0.0
        )

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period
    return adx


def calculate_macd(closes: list[float]) -> dict[str, float | None]:
    fast = ema_series(closes, 12)
    slow = ema_series(closes, 26)
    if not fast or not slow:
        return {"macd": None, "signal": None, "histogram": None}

    # Align the two EMA series at the point where the slower EMA starts.
    fast_aligned = fast[-len(slow):]
    macd_values = [fast_value - slow_value for fast_value, slow_value in zip(fast_aligned, slow)]
    signal = ema(macd_values, 9)
    macd_value = macd_values[-1]
    return {
        "macd": macd_value,
        "signal": signal,
        "histogram": macd_value - signal if signal is not None else None,
    }


def calculate_technical_indicators(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if not candles:
        return {}

    closes = [candle["close"] for candle in candles]
    latest = candles[-1]
    latest_close = latest["close"]
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    ema50 = ema(closes, 50)
    ema200 = ema(closes, 200)
    atr14 = calculate_atr(candles, 14)
    rsi14 = calculate_rsi(closes, 14)
    macd = calculate_macd(closes)
    adx14 = calculate_adx(candles, 14)

    bb_width_pct = None
    bb_percent_b = None
    bb_middle = None
    if len(closes) >= 20:
        recent_closes = closes[-20:]
        bb_middle = sum(recent_closes) / 20
        standard_deviation = statistics.pstdev(recent_closes)
        upper = bb_middle + (2 * standard_deviation)
        lower = bb_middle - (2 * standard_deviation)
        if bb_middle > 0:
            bb_width_pct = ((upper - lower) / bb_middle) * 100
        if upper != lower:
            bb_percent_b = (latest_close - lower) / (upper - lower)

    previous_volumes = [
        candle["quoteVolume"]
        for candle in candles[-21:-1]
        if candle["quoteVolume"] is not None
    ]
    average_quote_volume = (
        sum(previous_volumes) / len(previous_volumes) if previous_volumes else None
    )
    volume_ratio = (
        latest["quoteVolume"] / average_quote_volume
        if latest["quoteVolume"] is not None and average_quote_volume
        else None
    )

    recent = candles[-20:]
    taker_buy_quote = sum(
        candle["takerBuyQuoteVolume"] or 0.0 for candle in recent
    )
    total_quote = sum(candle["quoteVolume"] or 0.0 for candle in recent)
    taker_sell_quote = max(total_quote - taker_buy_quote, 0.0)
    taker_ratio = (
        taker_buy_quote / taker_sell_quote if taker_sell_quote > 0 else None
    )

    if ema9 is not None and ema21 is not None and ema50 is not None:
        if latest_close > ema9 > ema21 > ema50:
            ema_alignment = "bullish"
        elif latest_close < ema9 < ema21 < ema50:
            ema_alignment = "bearish"
        else:
            ema_alignment = "mixed"
    else:
        ema_alignment = None

    if rsi14 is None:
        rsi_state = None
    elif rsi14 >= 70:
        rsi_state = "overbought"
    elif rsi14 <= 30:
        rsi_state = "oversold"
    else:
        rsi_state = "neutral"

    return {
        "lastClosedCandleAt": utc_iso_from_milliseconds(latest["closeTime"]),
        "close": latest_close,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "ema200": ema200,
        "emaAlignment": ema_alignment,
        "rsi14": rsi14,
        "rsiState": rsi_state,
        "macd": macd["macd"],
        "macdSignal": macd["signal"],
        "macdHistogram": macd["histogram"],
        "adx14": adx14,
        "atr14": atr14,
        "atr14Pct": (atr14 / latest_close * 100) if atr14 is not None else None,
        "bollingerWidthPct": bb_width_pct,
        "bollingerPercentB": bb_percent_b,
        "bollingerMiddle": bb_middle,
        "lastQuoteVolumeUsd": latest["quoteVolume"],
        "lastTradeCount": latest["trades"],
        "lastVolumeVs20Average": volume_ratio,
        "takerBuySellRatio20": taker_ratio,
    }


def calculate_realized_volatility(
    candles: list[dict[str, Any]],
    *,
    window_returns: int,
    periods_per_year: int,
) -> dict[str, Any] | None:
    """Return both realised movement over the window and annualised sample volatility."""
    if len(candles) < window_returns + 1:
        return None

    closes = [candle["close"] for candle in candles[-(window_returns + 1):]]
    returns = [
        math.log(current / previous)
        for previous, current in zip(closes, closes[1:])
        if previous > 0 and current > 0
    ]
    if len(returns) < 2:
        return None

    period_volatility = math.sqrt(sum(value * value for value in returns)) * 100
    annualized = statistics.stdev(returns) * math.sqrt(periods_per_year) * 100
    return {
        "observations": len(returns),
        "periodVolatilityPct": period_volatility,
        "annualizedPct": annualized,
    }


def calculate_range_volatility(
    candles: list[dict[str, Any]],
    *,
    periods_per_year: int,
    window: int = 288,
) -> dict[str, float | None]:
    """Parkinson and Garman-Klass volatility from high/low/open/close ranges."""
    if len(candles) < window:
        return {"parkinson24hAnnualizedPct": None, "garmanKlass24hAnnualizedPct": None}
    sample = candles[-window:]

    log_hl_squared = [math.log(candle["high"] / candle["low"]) ** 2 for candle in sample]
    parkinson_variance = sum(log_hl_squared) / len(log_hl_squared) / (4 * math.log(2))
    parkinson = math.sqrt(max(parkinson_variance, 0.0) * periods_per_year) * 100

    gk_variances = []
    for candle in sample:
        log_hl = math.log(candle["high"] / candle["low"])
        log_co = math.log(candle["close"] / candle["open"])
        variance = 0.5 * (log_hl**2) - (2 * math.log(2) - 1) * (log_co**2)
        gk_variances.append(variance)
    gk_mean_variance = max(sum(gk_variances) / len(gk_variances), 0.0)
    garman_klass = math.sqrt(gk_mean_variance * periods_per_year) * 100

    return {
        "parkinson24hAnnualizedPct": parkinson,
        "garmanKlass24hAnnualizedPct": garman_klass,
    }


def calculate_price_change(candles: list[dict[str, Any]], bars: int) -> float | None:
    if len(candles) <= bars:
        return None
    previous_close = candles[-(bars + 1)]["close"]
    current_close = candles[-1]["close"]
    if previous_close <= 0:
        return None
    return (current_close / previous_close - 1) * 100


# ---------------------------------------------------------------------------
# Derivatives, order book and market flow
# ---------------------------------------------------------------------------
def calculate_open_interest_changes(history: Any) -> dict[str, Any]:
    rows = history if isinstance(history, list) else []
    rows = sorted(
        [row for row in rows if isinstance(row, dict) and as_float(row.get("sumOpenInterestValue")) is not None],
        key=lambda row: as_float(row.get("timestamp")) or 0,
    )
    result: dict[str, Any] = {
        "history": [],
        "change15mPct": None,
        "change1hPct": None,
        "change4hPct": None,
        "change24hPct": None,
    }
    if not rows:
        return result

    result["history"] = [
        {
            "time": utc_iso_from_milliseconds(row.get("timestamp")),
            "openInterestBtc": as_float(row.get("sumOpenInterest")),
            "openInterestUsd": as_float(row.get("sumOpenInterestValue")),
        }
        for row in rows
    ]

    latest_value = as_float(rows[-1].get("sumOpenInterestValue"))
    if latest_value is None or latest_value <= 0:
        return result

    for label, periods_back in (("15m", 1), ("1h", 4), ("4h", 16), ("24h", 96)):
        if len(rows) > periods_back:
            old_value = as_float(rows[-(periods_back + 1)].get("sumOpenInterestValue"))
            if old_value is not None and old_value > 0:
                result[f"change{label}Pct"] = (latest_value / old_value - 1) * 100
    return result


def summarize_taker_flow(history: Any) -> dict[str, Any]:
    rows = history if isinstance(history, list) else []
    rows = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: as_float(row.get("timestamp")) or 0,
    )

    def summarize(sample: list[dict[str, Any]]) -> dict[str, Any] | None:
        buy_volume = sum(as_float(row.get("buyVol")) or 0.0 for row in sample)
        sell_volume = sum(as_float(row.get("sellVol")) or 0.0 for row in sample)
        total = buy_volume + sell_volume
        if total <= 0:
            return None
        return {
            "buyVolumeBtc": buy_volume,
            "sellVolumeBtc": sell_volume,
            "buySellRatio": buy_volume / sell_volume if sell_volume > 0 else None,
            "netBuyPct": (buy_volume - sell_volume) / total * 100,
            "lastPeriodAt": utc_iso_from_milliseconds(sample[-1].get("timestamp")),
        }

    def summarize_complete_window(periods: int) -> dict[str, Any] | None:
        return summarize(rows[-periods:]) if len(rows) >= periods else None

    return {
        "last5m": summarize_complete_window(1),
        "last15m": summarize_complete_window(3),
        "last1h": summarize_complete_window(12),
        "last4h": summarize_complete_window(48),
        "history": [
            {
                "time": utc_iso_from_milliseconds(row.get("timestamp")),
                "buySellRatio": as_float(row.get("buySellRatio")),
                "buyVol": as_float(row.get("buyVol")),
                "sellVol": as_float(row.get("sellVol")),
            }
            for row in rows
        ],
    }


def normalize_ratio_history(history: Any) -> dict[str, Any] | None:
    rows = history if isinstance(history, list) else []
    rows = sorted(
        [row for row in rows if isinstance(row, dict)],
        key=lambda row: as_float(row.get("timestamp")) or 0,
    )
    if not rows:
        return None

    latest = rows[-1]
    return {
        "longShortRatio": as_float(latest.get("longShortRatio")),
        "longAccountPct": (
            as_float(latest.get("longAccount")) * 100
            if as_float(latest.get("longAccount")) is not None
            else None
        ),
        "shortAccountPct": (
            as_float(latest.get("shortAccount")) * 100
            if as_float(latest.get("shortAccount")) is not None
            else None
        ),
        "timestamp": utc_iso_from_milliseconds(latest.get("timestamp")),
        "history": [
            {
                "time": utc_iso_from_milliseconds(row.get("timestamp")),
                "longShortRatio": as_float(row.get("longShortRatio")),
            }
            for row in rows
        ],
    }


def calculate_order_book_metrics(order_book: Any) -> dict[str, Any] | None:
    if not isinstance(order_book, dict):
        return None

    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    if not bids or not asks:
        return None

    parsed_bids = [
        (as_float(level[0]), as_float(level[1]))
        for level in bids
        if isinstance(level, list) and len(level) >= 2
    ]
    parsed_asks = [
        (as_float(level[0]), as_float(level[1]))
        for level in asks
        if isinstance(level, list) and len(level) >= 2
    ]
    parsed_bids = [(price, qty) for price, qty in parsed_bids if price and qty is not None]
    parsed_asks = [(price, qty) for price, qty in parsed_asks if price and qty is not None]
    if not parsed_bids or not parsed_asks:
        return None

    best_bid = max(price for price, _ in parsed_bids)
    best_ask = min(price for price, _ in parsed_asks)
    mid_price = (best_bid + best_ask) / 2
    if mid_price <= 0:
        return None

    spread_bps = (best_ask - best_bid) / mid_price * 10_000
    depth: dict[str, Any] = {}

    for band_pct in (0.1, 0.25, 0.5, 1.0):
        bid_notional = sum(
            price * quantity
            for price, quantity in parsed_bids
            if price >= mid_price * (1 - band_pct / 100)
        )
        ask_notional = sum(
            price * quantity
            for price, quantity in parsed_asks
            if price <= mid_price * (1 + band_pct / 100)
        )
        total = bid_notional + ask_notional
        depth[f"bidDepthUsdWithin{band_pct:g}Pct"] = bid_notional
        depth[f"askDepthUsdWithin{band_pct:g}Pct"] = ask_notional
        depth[f"imbalanceWithin{band_pct:g}Pct"] = (
            (bid_notional - ask_notional) / total if total > 0 else None
        )

    return {
        "bestBid": best_bid,
        "bestAsk": best_ask,
        "midPrice": mid_price,
        "spreadBps": spread_bps,
        "bidQtyAtBest": next((qty for price, qty in parsed_bids if price == best_bid), None),
        "askQtyAtBest": next((qty for price, qty in parsed_asks if price == best_ask), None),
        **depth,
        "warning": "Snapshot of visible Binance Futures depth; orders can be cancelled or spoofed.",
    }


# ---------------------------------------------------------------------------
# Main market data aggregation
# ---------------------------------------------------------------------------
async def load_market_data() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    start_ms = int((now - timedelta(days=14)).timestamp() * 1000)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0),
        follow_redirects=True,
        headers={"User-Agent": "CryptoMarketDashboard/2.0"},
    ) as client:
        requests = {
            "dvol": fetch_json(
                client,
                f"{DERIBIT_API}/public/get_volatility_index_data",
                params={
                    "currency": "BTC",
                    "start_timestamp": start_ms,
                    "end_timestamp": now_ms,
                    "resolution": "60",
                },
            ),
            "deribit_index": fetch_json(
                client,
                f"{DERIBIT_API}/public/get_index_price",
                params={"index_name": "btc_usd"},
            ),
            "premium_index": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/premiumIndex",
                params={"symbol": "BTCUSDT"},
            ),
            "open_interest": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/openInterest",
                params={"symbol": "BTCUSDT"},
            ),
            "open_interest_history": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/futures/data/openInterestHist",
                params={"symbol": "BTCUSDT", "period": "15m", "limit": 100},
            ),
            "global_long_short": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/futures/data/globalLongShortAccountRatio",
                params={"symbol": "BTCUSDT", "period": "5m", "limit": 60},
            ),
            "taker_flow": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/futures/data/takerlongshortRatio",
                params={"symbol": "BTCUSDT", "period": "5m", "limit": 60},
            ),
            "order_book": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/depth",
                params={"symbol": "BTCUSDT", "limit": 100},
            ),
            "ticker_24h": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/ticker/24hr",
                params={"symbol": "BTCUSDT"},
            ),
            "adl_risk": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/symbolAdlRisk",
                params={"symbol": "BTCUSDT"},
            ),
            "klines_1m": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "1m", "limit": 500},
            ),
            "klines_5m": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "5m", "limit": 300},
            ),
            "klines_15m": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "15m", "limit": 200},
            ),
            "klines_1h": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "1h", "limit": 750},
            ),
            "klines_4h": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "4h", "limit": 200},
            ),
            "klines_1d": fetch_json(
                client,
                f"{BINANCE_FUTURES_API}/fapi/v1/klines",
                params={"symbol": "BTCUSDT", "interval": "1d", "limit": 60},
            ),
            "fear_greed": fetch_json(
                client,
                f"{ALTERNATIVE_API}/fng/",
                params={"limit": 1},
            ),
        }
        responses = await asyncio.gather(*requests.values())

    data = dict(zip(requests.keys(), responses))

    # Only completed candles are used for indicators to avoid distorting them
    # with a still-forming candle. /api/candles continues to return live candles.
    candles = {
        interval: parse_klines(data.get(f"klines_{interval}"), drop_incomplete=True, now_ms=now_ms)
        for interval in ("1m", "5m", "15m", "1h", "4h", "1d")
    }

    dvol_response = data.get("dvol")
    dvol_result = (
        dvol_response.get("result")
        if isinstance(dvol_response, dict)
        else None
    )
    dvol_rows = dvol_result.get("data", []) if isinstance(dvol_result, dict) else []
    dvol_history = []

    for row in dvol_rows:
        if not isinstance(row, list) or len(row) < 5:
            continue

        timestamp = as_float(row[0])
        open_value = as_float(row[1])
        high_value = as_float(row[2])
        low_value = as_float(row[3])
        close_value = as_float(row[4])

        if any(
            value is None
            for value in [
                timestamp,
                open_value,
                high_value,
                low_value,
                close_value,
            ]
        ):
            continue

        point_time = utc_iso_from_milliseconds(timestamp)

        if point_time is None:
            continue

        dvol_history.append({
            "time": point_time,
            "timestamp": int(timestamp / 1000),
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,

            # Zachování kompatibility s původním grafem.
            "value": close_value,
        })

    dvol_history.sort(key=lambda point: point["timestamp"])
    dvol_history.sort(key=lambda point: point["time"])
    dvol = dvol_history[-1]["value"] if dvol_history else None

    deribit_index_result = (
        data["deribit_index"].get("result", {})
        if isinstance(data.get("deribit_index"), dict)
        else {}
    )
    btc_usd = as_float(deribit_index_result.get("index_price"))
    btc_usd_source = "deribit:btc_usd" if btc_usd is not None else None

    premium = data.get("premium_index") if isinstance(data.get("premium_index"), dict) else {}
    mark_price = as_float(premium.get("markPrice"))
    index_price = as_float(premium.get("indexPrice"))
    if btc_usd is None:
        btc_usd = index_price or mark_price
        btc_usd_source = "binance:indexPrice" if index_price is not None else (
            "binance:markPrice" if mark_price is not None else None
        )

    funding_rate = as_float(premium.get("lastFundingRate"))
    funding_rate_pct = funding_rate * 100 if funding_rate is not None else None
    basis_pct = (
        (mark_price / index_price - 1) * 100
        if mark_price is not None and index_price is not None and index_price > 0
        else None
    )

    open_interest_response = data.get("open_interest")
    open_interest_btc = (
        as_float(open_interest_response.get("openInterest"))
        if isinstance(open_interest_response, dict)
        else None
    )
    open_interest_usd = (
        open_interest_btc * btc_usd
        if open_interest_btc is not None and btc_usd is not None
        else None
    )
    open_interest_trend = calculate_open_interest_changes(data.get("open_interest_history"))

    global_long_short = normalize_ratio_history(data.get("global_long_short"))
    taker_flow = summarize_taker_flow(data.get("taker_flow"))
    order_book = calculate_order_book_metrics(data.get("order_book"))

    ticker = data.get("ticker_24h") if isinstance(data.get("ticker_24h"), dict) else {}
    ticker_last_price = as_float(ticker.get("lastPrice"))
    ticker_high = as_float(ticker.get("highPrice"))
    ticker_low = as_float(ticker.get("lowPrice"))
    ticker_24h = {
        "priceChange": as_float(ticker.get("priceChange")),
        "priceChangePct": as_float(ticker.get("priceChangePercent")),
        "high": ticker_high,
        "low": ticker_low,
        "baseVolumeBtc": as_float(ticker.get("volume")),
        "quoteVolumeUsd": as_float(ticker.get("quoteVolume")),
        "tradeCount": as_float(ticker.get("count")),
        "highLowRangePct": (
            (ticker_high - ticker_low) / ticker_last_price * 100
            if ticker_high is not None and ticker_low is not None
            and ticker_last_price is not None and ticker_last_price > 0
            else None
        ),
        "distanceFromHighPct": (
            (ticker_last_price / ticker_high - 1) * 100
            if ticker_last_price is not None and ticker_high is not None and ticker_high > 0
            else None
        ),
        "distanceFromLowPct": (
            (ticker_last_price / ticker_low - 1) * 100
            if ticker_last_price is not None and ticker_low is not None and ticker_low > 0
            else None
        ),
    }

    technical = {
        interval: calculate_technical_indicators(candles[interval])
        for interval in ("1m", "5m", "15m", "1h", "4h", "1d")
    }

    realized_volatility = {
        "1h": calculate_realized_volatility(
            candles["1m"], window_returns=60, periods_per_year=525_600
        ),
        "4h": calculate_realized_volatility(
            candles["1m"], window_returns=240, periods_per_year=525_600
        ),
        "24h": calculate_realized_volatility(
            candles["5m"], window_returns=288, periods_per_year=105_120
        ),
        "7d": calculate_realized_volatility(
            candles["1h"], window_returns=168, periods_per_year=8_760
        ),
        "30d": calculate_realized_volatility(
            candles["1h"], window_returns=720, periods_per_year=8_760
        ),
    }
    range_volatility = calculate_range_volatility(
        candles["5m"], periods_per_year=105_120, window=288
    )

    price_changes = {
        "5m": calculate_price_change(candles["1m"], 5),
        "15m": calculate_price_change(candles["1m"], 15),
        "1h": calculate_price_change(candles["1m"], 60),
        "4h": calculate_price_change(candles["1m"], 240),
        "24h": ticker_24h["priceChangePct"],
    }

    realized_vol_30d = (
        realized_volatility["30d"]["annualizedPct"]
        if realized_volatility["30d"] is not None
        else None
    )
    dvol_rv_spread = (
        dvol - realized_vol_30d
        if dvol is not None and realized_vol_30d is not None
        else None
    )
    dvol_rv_ratio = (
        dvol / realized_vol_30d
        if dvol is not None and realized_vol_30d is not None and realized_vol_30d > 0
        else None
    )

    fear_greed_item = None
    fear_greed_response = data.get("fear_greed")
    if isinstance(fear_greed_response, dict):
        items = fear_greed_response.get("data", [])
        if isinstance(items, list) and items:
            fear_greed_item = items[0]

    fear_greed = None
    if isinstance(fear_greed_item, dict):
        timestamp = fear_greed_item.get("timestamp")
        fear_greed = {
            "value": as_float(fear_greed_item.get("value")),
            "classification": fear_greed_item.get("value_classification"),
            "updatedAt": utc_iso_from_milliseconds(
                int(timestamp) * 1000
            ) if timestamp else None,
        }

    adl_risk_response = data.get("adl_risk")
    adl_risk = None
    if isinstance(adl_risk_response, dict):
        adl_risk = {
            "rating": adl_risk_response.get("adlRisk"),
            "updatedAt": utc_iso_from_milliseconds(adl_risk_response.get("updateTime")),
        }

    has_any_data = any(
        value is not None
        for value in (btc_usd, dvol, funding_rate_pct, open_interest_usd, fear_greed)
    ) or any(candles.values())
    if not has_any_data:
        raise HTTPException(
            status_code=502,
            detail="Could not retrieve data from any market data provider.",
        )

    return {
        # Backwards-compatible fields used by the existing frontend.
        "updatedAt": now.isoformat().replace("+00:00", "Z"),
        "btcUsd": btc_usd,
        "btcUsdSource": btc_usd_source,
        "dvol": dvol,
        "realizedVol30dPct": realized_vol_30d,
        "fundingRatePct": funding_rate_pct,
        "openInterestBtc": open_interest_btc,
        "openInterestUsd": open_interest_usd,
        "dvolHistory": dvol_history,
        "fearGreed": fear_greed,

        # Additional volatility data.
        "volatility": {
            "dvolAnnualizedPct": dvol,
            "realized": realized_volatility,
            **range_volatility,
            "dvolMinusRealized30dPctPoints": dvol_rv_spread,
            "dvolToRealized30dRatio": dvol_rv_ratio,
            "interpretation": {
                "dvol": "Deribit implied volatility; market-priced expectation, not a forecast of direction.",
                "realized": "Close-to-close realised volatility. periodVolatilityPct is the realised move over the window; annualizedPct is annualised standard deviation.",
                "parkinson": "Range-based volatility from high-low prices; less sensitive to close-only moves.",
                "garmanKlass": "Range-based OHLC volatility estimate.",
            },
        },
        "priceChangesPct": price_changes,
        "technical": technical,
        "derivatives": {
            "markPrice": mark_price,
            "indexPrice": index_price,
            "basisPct": basis_pct,
            "nextFundingAt": utc_iso_from_milliseconds(premium.get("nextFundingTime")),
            "openInterest": {
                "currentBtc": open_interest_btc,
                "currentUsd": open_interest_usd,
                **open_interest_trend,
            },
            "globalLongShortAccounts": global_long_short,
            "takerFlow": taker_flow,
            "adlRisk": adl_risk,
        },
        "orderBook": order_book,
        "ticker24h": ticker_24h,
        "dataAvailability": {
            "deribitDvol": dvol is not None,
            "deribitIndex": deribit_index_result.get("index_price") is not None,
            "binanceFuturesCandles": bool(candles["1m"]),
            "openInterest": open_interest_btc is not None,
            "openInterestHistory": bool(open_interest_trend["history"]),
            "globalLongShort": global_long_short is not None,
            "takerFlow": taker_flow["last5m"] is not None,
            "orderBook": order_book is not None,
            "fearGreed": fear_greed is not None,
        },
        "sources": {
            "priceAndDeribitVolatility": "Deribit public API (no API key)",
            "technicalAndFuturesData": "Binance USD-M Futures public market data (no API key)",
            "sentiment": "Alternative.me Fear & Greed public API",
        },
    }


@app.get("/api/market")
async def get_market():
    now = time.monotonic()
    if _market_cache["value"] is not None and now < _market_cache["expires_at"]:
        return _market_cache["value"]

    async with _market_cache_lock:
        now = time.monotonic()
        if _market_cache["value"] is not None and now < _market_cache["expires_at"]:
            return _market_cache["value"]

        data = await load_market_data()
        try:
            await asyncio.to_thread(save_market_snapshot, data)
        except Exception:
            logger.exception("Could not save market snapshot to DuckDB.")

        _market_cache["value"] = data
        _market_cache["expires_at"] = time.monotonic() + CACHE_TTL_SECONDS
        return data


# ---------------------------------------------------------------------------
# Candle endpoint - old fields are retained; volume/flow fields are added
# ---------------------------------------------------------------------------
CANDLE_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}


@app.get("/api/candles")
async def get_candles(interval: str = Query(default="15m")):
    if interval not in CANDLE_INTERVALS:
        raise HTTPException(status_code=422, detail="Unsupported candle interval.")

    async with httpx.AsyncClient(timeout=12.0) as client:
        rows = await fetch_json(
            client,
            f"{BINANCE_FUTURES_API}/fapi/v1/klines",
            params={"symbol": "BTCUSDT", "interval": interval, "limit": 500},
        )

    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="Could not retrieve candles.")

    candles = parse_klines(rows, drop_incomplete=False)
    return {
        "symbol": "BTCUSDT",
        "interval": interval,
        "source": "Binance USD-M Futures",
        "candles": [
            {
                "time": candle["openTime"] // 1000,
                "closeTime": candle["closeTime"] // 1000,
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
                "quoteVolume": candle["quoteVolume"],
                "trades": candle["trades"],
                "takerBuyVolume": candle["takerBuyVolume"],
                "takerBuyQuoteVolume": candle["takerBuyQuoteVolume"],
            }
            for candle in candles
        ],
    }
