# BTC Market Intelligence Dashboard

A cryptocurrency market dashboard focused on **Bitcoin (BTC)**, combining spot-index data, futures-market metrics, volatility analytics, sentiment data, and interactive price charts.

The project is built as a small full-stack application: a **Next.js / React / TypeScript** frontend and a **FastAPI / Python** backend. It currently uses public market-data endpoints and does not require exchange API keys.

> **Project status:** Work in progress. The README describes the implementation visible in the source archive supplied for review. Some backend metrics are available through the API but are not necessarily exposed in the current UI. Order-flow indicators such as VWAP, CVD, Liquidation Pressure, and Volume Profile are not implemented yet.

## Highlights

- Bitcoin market overview with current price and key market metrics
- Interactive BTCUSDT candlestick chart with selectable time intervals
- Live candle updates through Binance Futures WebSocket
- Deribit DVOL history displayed as a candlestick chart
- Realized volatility and implied-volatility data
- Fear & Greed sentiment index
- Futures-market data including funding, open interest, taker flow, long/short ratios, and order-book metrics in the backend response
- Cryptocurrency news feed with symbol filtering
- Local market snapshots stored in DuckDB

## Screens and data

The current market panel displays:

| Metric | Description |
|---|---|
| BTC price | Bitcoin index price retrieved from Deribit |
| DVOL | Deribit implied-volatility index |
| Realized volatility | Historical, annualized volatility calculated from market candles |
| Fear & Greed | Sentiment index provided by Alternative.me |
| Open interest | Approximate USD value of open BTC futures positions |
| DVOL history | Historical volatility-index candles |

The backend also calculates or retrieves additional information, including technical indicators, price changes, volatility estimates, derivatives metrics, order-book metrics, and 24-hour futures statistics.

## Technology stack

### Frontend

- [Next.js](https://nextjs.org/)
- [React](https://react.dev/)
- [TypeScript](https://www.typescriptlang.org/)
- [Lightweight Charts](https://tradingview.github.io/lightweight-charts/)
- [Tailwind CSS](https://tailwindcss.com/)
- [pnpm](https://pnpm.io/)

### Backend

- [Python](https://www.python.org/)
- [FastAPI](https://fastapi.tiangolo.com/)
- [HTTPX](https://www.python-httpx.org/)
- [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/)
- [DuckDB](https://duckdb.org/)
- [Uvicorn](https://www.uvicorn.org/)

## Architecture

```text
.
├── apps/
│   ├── api/
│   │   ├── app/
│   │   │   └── main.py          # FastAPI application and market-data logic
│   │   ├── storage.py           # DuckDB persistence
│   │   ├── requirements.txt
│   │   └── data/                # Created locally for the DuckDB database
│   └── web/
│       ├── app/
│       │   ├── components/
│       │   │   ├── MarketPanel.tsx
│       │   │   ├── PriceChart.tsx
│       │   │   └── ...
│       │   └── types/
│       │       └── marketData.ts
│       └── package.json
└── README.md
```

The tree above is a simplified overview, not a complete listing of every file.

### Data flow

1. The Next.js frontend requests market data from its `/api/market` and `/api/candles` routes.
2. The FastAPI backend retrieves data from public provider APIs.
3. The backend normalizes provider responses and calculates derived metrics.
4. Market snapshots are written to a local DuckDB database.
5. The frontend renders the market overview and charts.
6. The price chart opens a Binance Futures WebSocket connection to receive live candle updates.

**Important:** The frontend API paths must be routed to the FastAPI service (for example, through Next.js rewrites or route handlers). The proxy configuration was not included in the source archive used to prepare this README, so verify that it is present in your local project.

## Data providers

| Provider | Data used |
|---|---|
| [Deribit public API](https://docs.deribit.com/) | BTC index price and DVOL history |
| [Binance USDⓈ-M Futures API](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data) | Candles, mark price, funding, open interest, long/short statistics, taker flow, order book, and 24-hour statistics |
| [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/) | Crypto Fear & Greed Index |
| [Investing.com RSS](https://www.investing.com/) | Cryptocurrency news feed |

No API keys are required by the data-source integrations currently visible in the backend.

### Market-data caveat

The dashboard combines data from different venues and instruments. For example, the displayed BTC index price comes from Deribit, while candles and derivatives metrics are based on Binance BTCUSDT USDⓈ-M Futures.

These values are related, but they are **not the same market feed**. Small differences can occur because of venue, instrument, liquidity, and timing. Do not interpret the combined dashboard as a single-exchange order book or as execution data from a broker.

## Getting started

### Prerequisites

- Python 3.10 or newer
- Node.js version supported by the installed Next.js release
- pnpm (the frontend package declares `pnpm@12.9.1`)
- Internet access to reach the public data providers

### 1. Start the backend

Open a terminal:

```bash
cd apps/api
```

Create and activate a virtual environment.

**Windows PowerShell:**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Start FastAPI from the `apps/api` directory:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Useful backend URLs:

- Health check: `http://127.0.0.1:8000/health`
- Interactive API documentation: `http://127.0.0.1:8000/docs`
- Market data: `http://127.0.0.1:8000/api/market`
- Candles: `http://127.0.0.1:8000/api/candles?interval=15m`
- News: `http://127.0.0.1:8000/api/news?symbol=BTC&limit=20`

The DuckDB database is created locally under `apps/api/data/` when the backend initializes.

### 2. Start the frontend

In a second terminal:

```bash
cd apps/web
pnpm install
pnpm dev
```

Open [http://localhost:3000](http://localhost:3000).

If the frontend displays API errors, check that the FastAPI backend is running and that the Next.js API proxy routes forward `/api/market`, `/api/candles`, and any news requests to the backend.

## API overview

The FastAPI application currently exposes these endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Backend health check |
| `GET` | `/api/market` | Aggregated market overview and derived metrics |
| `GET` | `/api/candles?interval=15m` | Historical futures candles |
| `GET` | `/api/news?symbol=BTC&limit=20` | Filtered cryptocurrency news |

Supported candle intervals:

```text
1m, 5m, 15m, 30m, 1h, 4h, 1d
```

Supported news symbols:

```text
all, BTC, ETH, XRP, SOL, DOGE, ADA
```

The market endpoint uses a short in-memory cache (30 seconds) to reduce repeated upstream requests.

## Current limitations

- The dashboard depends on third-party public APIs and RSS feeds. Provider downtime, rate limits, or response changes can affect data availability.
- Market data from Deribit and Binance is combined for analysis; it should not be treated as one unified exchange feed.
- Candle-based volume and taker-flow data have less granularity than individual trade-level data.
- The supplied `requirements.txt` does not currently list `httpx` and `duckdb`, although the backend imports both. They are included explicitly in the setup instructions above; the dependency file should be updated.
- The frontend proxy configuration was not present in the source archive reviewed for this README.
- The current source snapshot does not contain implementations of VWAP, CVD, Liquidation Pressure, or Volume Profile.

## Roadmap

Potential next steps:

- [ ] Add session-based VWAP with configurable bands
- [ ] Add Cumulative Volume Delta (CVD) using futures taker-buy volume
- [ ] Add Volume Profile with Point of Control (POC), Value Area High (VAH), and Value Area Low (VAL)
- [ ] Add a liquidation-event stream and liquidation-pressure visualization
- [ ] Make data-source and instrument labels explicit throughout the UI
- [ ] Add configurable chart layouts and indicator visibility
- [ ] Add automated backend and frontend tests
- [ ] Improve resilience to upstream API failures and missing data

Indicator calculations should clearly communicate their data granularity and limitations. In particular, candle-derived CVD and Volume Profile are approximations; trade-level data is needed for more precise order-flow analysis.

## Development

Frontend scripts, run from `apps/web`:

```bash
pnpm dev       # Start the development server
pnpm build     # Create a production build
pnpm start     # Run the production server
pnpm lint      # Run ESLint
```

Backend development server, run from `apps/api`:

```bash
uvicorn app.main:app --reload
```

## Disclaimer

This project is for **market analysis, experimentation, and educational purposes**. It is not financial advice and does not provide a guarantee of data accuracy, completeness, or availability. Cryptocurrency markets are volatile. Always verify market information with the venue and instrument relevant to your own trading decisions.

## License

No license file was included in the supplied project archive. Add a `LICENSE` file before presenting the repository as open source, and choose a license that matches how you want others to use the code.
