import { useEffect, useMemo, useState } from "react";
import type { Candle } from "../PriceChart";
import styles from "./OrderFlowPanel.module.css";

type LiquidationEvent = {
  side: "BUY" | "SELL";
  price: number;
  quantity: number;
  notional: number;
  time: number;
};

function calculateFlow(candles: Candle[]) {
  let cvd = 0;
  let buyVolume = 0;
  let sellVolume = 0;
  const latestDay = candles.length
    ? new Date(candles[candles.length - 1].time * 1000).toISOString().slice(0, 10)
    : "";

  const currentDayCandles = candles.filter(
    (candle) =>
      new Date(candle.time * 1000).toISOString().slice(0, 10) === latestDay
  );

  for (const candle of currentDayCandles) {
    const totalQuote = candle.quoteVolume ?? 0;
    const takerBuyQuote = candle.takerBuyQuoteVolume ?? 0;
    const takerSellQuote = Math.max(0, totalQuote - takerBuyQuote);
    buyVolume += takerBuyQuote;
    sellVolume += takerSellQuote;
    cvd += takerBuyQuote - takerSellQuote;
  }

  return { cvd, buyVolume, sellVolume, latestDay };
}

function calculateVolumeProfile(candles: Candle[], bins = 24) {
  if (!candles.length) return [];
  const low = Math.min(...candles.map((c) => c.low));
  const high = Math.max(...candles.map((c) => c.high));
  if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) return [];

  const step = (high - low) / bins;
  const levels = Array.from({ length: bins }, (_, index) => ({
    price: low + step * (index + 0.5),
    volume: 0,
  }));

  for (const candle of candles) {
    const typicalPrice = (candle.high + candle.low + candle.close) / 3;
    const index = Math.min(
      bins - 1,
      Math.max(0, Math.floor((typicalPrice - low) / step))
    );
    levels[index].volume += candle.volume ?? 0;
  }

  return levels;
}

export default function OrderFlowPanel({ candles }: { candles: Candle[] }) {
  const [liquidations, setLiquidations] = useState<LiquidationEvent[]>([]);
  const flow = useMemo(() => calculateFlow(candles), [candles]);
  const profile = useMemo(() => calculateVolumeProfile(candles), [candles]);
  const maxProfileVolume = Math.max(1, ...profile.map((level) => level.volume));

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket("wss://fstream.binance.com/ws/btcusdt@forceOrder");

      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          const order = message.o;
          if (!order) return;
          const price = Number(order.ap || order.p);
          const quantity = Number(order.q);
          if (!(price > 0) || !(quantity > 0)) return;

          const item: LiquidationEvent = {
            side: order.S === "BUY" ? "BUY" : "SELL",
            price,
            quantity,
            notional: price * quantity,
            time: Number(order.T || message.E || Date.now()),
          };

          setLiquidations((previous) =>
            [...previous, item].filter((entry) => entry.time > Date.now() - 5 * 60_000).slice(-300)
          );
        } catch {
          // Ignore malformed events.
        }
      };

      socket.onclose = () => {
        if (!stopped) reconnectTimer = setTimeout(connect, 5000);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  const longLiquidations = liquidations
    .filter((item) => item.side === "SELL")
    .reduce((sum, item) => sum + item.notional, 0);
  const shortLiquidations = liquidations
    .filter((item) => item.side === "BUY")
    .reduce((sum, item) => sum + item.notional, 0);

  return (
    <section className={styles.panel}>
      <header className={styles.header}>
        <h2>Order flow · BTCUSDT Futures</h2>
        <span>Binance USD-M · veřejná data</span>
      </header>

      <div className={styles.metrics}>
        <article>
          <span>CVD · dnešní UTC session</span>
          <strong className={flow.cvd >= 0 ? styles.positive : styles.negative}>
            {flow.cvd >= 0 ? "+" : "−"}
            {Math.abs(flow.cvd).toLocaleString("en-US", { maximumFractionDigits: 0 })} USDT
          </strong>
          <small>Agresivní nákupy minus agresivní prodeje</small>
        </article>
        <article>
          <span>Taker buy volume</span>
          <strong>{flow.buyVolume.toLocaleString("en-US", { maximumFractionDigits: 0 })} USDT</strong>
        </article>
        <article>
          <span>Taker sell volume</span>
          <strong>{flow.sellVolume.toLocaleString("en-US", { maximumFractionDigits: 0 })} USDT</strong>
        </article>
      </div>

      <div className={styles.columns}>
        <div>
          <h3>Volume Profile · aktuálně načtené svíčky</h3>
          <p className={styles.note}>
            Aproximace: objem každé svíčky je přiřazen její typické ceně.
          </p>
          <div className={styles.profile}>
            {profile.slice().reverse().map((level) => (
              <div className={styles.profileRow} key={level.price}>
                <span>{level.price.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
                <div className={styles.track}>
                  <div
                    className={styles.bar}
                    style={{ width: `${(level.volume / maxProfileVolume) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h3>Liquidation Pressure · posledních 5 minut</h3>
          <p className={styles.note}>
            BUY likvidace obvykle zavírají shorty; SELL likvidace longy.
          </p>
          <div className={styles.liquidationMetric}>
            <span>Likvidované long pozice</span>
            <strong className={styles.negative}>
              {longLiquidations.toLocaleString("en-US", { maximumFractionDigits: 0 })} USDT
            </strong>
          </div>
          <div className={styles.liquidationMetric}>
            <span>Likvidované short pozice</span>
            <strong className={styles.positive}>
              {shortLiquidations.toLocaleString("en-US", { maximumFractionDigits: 0 })} USDT
            </strong>
          </div>
          <p className={styles.note}>
            Binance forceOrder stream poskytuje omezené aktualizace, nikoli úplný záznam všech likvidací.
          </p>
        </div>
      </div>
    </section>
  );
}
