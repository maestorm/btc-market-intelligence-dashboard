import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  type UTCTimestamp,
} from "lightweight-charts";

export type Candle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
};

export default function PriceChart({
  data,
  interval,
}: {
  data: Candle[];
  interval: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 280,
    });

    const series = chart.addSeries(CandlestickSeries);

    series.setData(
      data.map((candle) => ({
        ...candle,
        time: candle.time as UTCTimestamp,
      }))
    );

      const visibleBars = 80;

    if (data.length > 1) {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, data.length - visibleBars),
        to: data.length - 1,
      });
    }
    // chart.timeScale().fitContent();

    const socket = new WebSocket(
      `wss://fstream.binance.com/market/ws/btcusdt@kline_${interval}`
    );

    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      const k = message.k;

      if (!k) return;

      series.update({
        time: Math.floor(k.t / 1000) as UTCTimestamp,
        open: Number(k.o),
        high: Number(k.h),
        low: Number(k.l),
        close: Number(k.c),
      });
    };

    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      socket.close();
      chart.remove();
    };
  }, [data, interval]);

  return <div ref={containerRef} />;
}