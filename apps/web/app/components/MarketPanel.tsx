"use client";

import { useEffect, useState, useTransition } from "react";
import DvolCandlestickChart from "./DvolCandlestickChart";
import PriceChart from "./PriceChart";
import styles from "./MarketPanel.module.css";
import type { Candle } from "./PriceChart"
import OrderFlowPanel from "./order-flow/OrderFlowPanel";

import type { MarketData } from "@/types/marketData"

function formatNumber(value: number | null, suffix = "") {
  return value == null
    ? "—"
    : `${value.toLocaleString("cs-CZ", { maximumFractionDigits: 2 })}${suffix}`;
}

// function formatFundingRate(value: number | null) {
//   if (value == null) return "—";

//   const formatted = Math.abs(value).toLocaleString("cs-CZ", {
//     minimumFractionDigits: 4,
//     maximumFractionDigits: 6,
//   });

//   return `${value > 0 ? "+" : value < 0 ? "−" : ""}${formatted} %`;
// }

export default function MarketPanel() {
  const [market, setMarket] = useState<MarketData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [interval, setInterval] = useState("15m");
  const [candles, setCandles] = useState<Candle[]>([]);
  const [isPending, startTransition] = useTransition();
  const [dvolRange, setDvolRange] = useState(180);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`/api/candles?interval=${encodeURIComponent(interval)}`, {
        cache: "no-store",
        signal: controller.signal,
    }).then(async (response) => {
        if (!response.ok) {
          throw new Error(`Načtení svíček selhalo: HTTP ${response.status}`);
        }
        return response.json();
      })
      .then((result) => setCandles(result.candles))
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Chyba při načítání.");
      });

      return () => controller.abort();
    }, [interval]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const loadMarket = async () => {
      try {
        const response = await fetch("/api/market", {
          cache: "no-store",
        });

        if (!response.ok) {
          throw new Error("Data trhu se nepodařilo načíst.");
        }

        const data = (await response.json()) as MarketData;

        if (!cancelled) {
          setMarket(data);
          setError(null);
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Chyba při načítání dat."
          );
        }
      } finally {
        if (!cancelled) {
          timer = setTimeout(loadMarket, 35_000);
        }
      }
    };

    void loadMarket();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);


  if (error) return <p role="alert">{error}</p>;
  if (!market) return <p>Načítám data trhu…</p>;

  return (
    <section>
      <select value={interval} onChange={(event) => setInterval(event.target.value)}>
        <option value="1m">1 minuta</option>
        <option value="5m">5 minut</option>
        <option value="15m">15 minut</option>
        <option value="1h">1 hodina</option>
        <option value="1d">1 den</option>
      </select>

      {isPending && <p aria-live="polite">Aktualizuji graf…</p>}
      <PriceChart data={candles} interval={interval} />
      <OrderFlowPanel candles={candles} />

      <div className={styles.metrics}>
        <Metric
          label="Cena BTC"
          value={formatNumber(market.btcUsd, " USD")}
          detail="Aktuální cena; pohyb sledujte ve svíčkovém grafu."
        />

        <Metric
          label="DVOL · očekávaná volatilita"
          value={formatNumber(market.dvol, "%")}
          detail="Volatilita očekávaná trhem z opcí."
        >
          <Meter value={market.dvol} max={100} />
        </Metric>

        <Metric
          label="Realizovaná volatilita · 30 dní"
          value={formatNumber(market.realizedVol30dPct, "%")}
          detail="Zpětně naměřená, anualizovaná volatilita."
        >
          <Meter value={market.realizedVol30dPct} max={100} />
        </Metric>

        <Metric
          label="Fear & Greed · nálada trhu"
          value={`${market.fearGreed?.value ?? "—"} / 100`}
          detail={market.fearGreed?.classification ?? "Nálada investorů"}
        >
          <Meter value={market.fearGreed?.value ?? null} max={100} />
          <small>Zdroj: Alternative.me</small>
        </Metric>

        <Metric
          label="Open interest"
          value={formatNumber(market.openInterestUsd, " USD")}
          detail="Přibližná hodnota otevřených BTC futures pozic."
        />
      </div>


    <div className={styles.chartCard}>
      <h2>DVOL · Implied Volatility</h2>

      <div className={styles.chartControls}>
        {[
          { label: "1H", value: 60 },
          { label: "3H", value: 180 },
          { label: "6H", value: 360 },
          { label: "12H", value: 720 },
          { label: "24H", value: 1440 },
        ].map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => setDvolRange(option.value)}
            aria-pressed={dvolRange === option.value}
          >
            {option.label}
          </button>
        ))}
      </div>

      <DvolCandlestickChart
        data={market.dvolHistory}
        visibleCandles={dvolRange}
      />

      <p className={styles.updated}>
        Aktualizováno:{" "}
        {new Date(market.updatedAt).toLocaleString("cs-CZ")}
      </p>
    </div>
    </section>
  );
}

function Metric({
  label,
  value,
  detail,
  children,
}: {
  label: string;
  value: string;
  detail: string;
  children?: React.ReactNode;
}) {
  return (
    <article className={styles.metric}>
      <p className={styles.metricLabel}>{label}</p>
      <strong className={styles.metricValue}>{value}</strong>
      <p className={styles.metricDetail}>{detail}</p>
      {children}
    </article>
  );
}

function Meter({
  value,
  max,
}: {
  value: number | null;
  max: number;
}) {
  if (value == null) return null;

  const width = Math.max(0, Math.min(100, (value / max) * 100));

  return (
    <div
      className={styles.meter}
      role="meter"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <span className={styles.meterFill} style={{ width: `${width}%` }} />
    </div>
  );
}

function FundingMeter({ value }: { value: number | null }) {
  if (value == null) return null;

  // Zobrazené měřítko je −0,05 % až +0,05 %; mimo něj se ukazatel zastaví na kraji.
  const position = Math.max(0, Math.min(100, ((value + 0.05) / 0.1) * 100));

  return (
    <div className={styles.fundingTrack} aria-label={`Funding rate ${value}%`}>
      <span className={styles.fundingZero} />
      <span
        className={styles.fundingMarker}
        style={{ left: `${position}%` }}
      />
    </div>
  );
}

