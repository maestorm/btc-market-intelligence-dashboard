"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import type { DvolCandle } from "@/types/marketData";

type Props = {
  data: DvolCandle[];
  visibleCandles: number;
};

export default function DvolCandlestickChart({
  data, 
  visibleCandles = 180
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  // Inicializace grafu – pouze jednou.
  useEffect(() => {
    const container = containerRef.current;

    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 320,

      layout: {
        background: {
          type: ColorType.Solid,
          color: "rgba(0,0,0,0)",
        },
        textColor: "#94a3b8",
      },

      grid: {
        vertLines: {
          color: "rgba(148,163,184,0.10)",
        },
        horzLines: {
          color: "rgba(148,163,184,0.10)",
        },
      },

      rightPriceScale: {
        borderVisible: false,
      },

      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
      },

      localization: {
        priceFormatter: (value: number) =>
          `${value.toFixed(2)} %`,
      },

      crosshair: {
        vertLine: {
          labelBackgroundColor: "#475569",
        },
        horzLine: {
          labelBackgroundColor: "#475569",
        },
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#16a34a",
      downColor: "#dc2626",

      borderVisible: false,

      wickUpColor: "#16a34a",
      wickDownColor: "#dc2626",

      priceLineVisible: true,
      lastValueVisible: true,

      priceFormat: {
        type: "custom",
        minMove: 0.01,
        formatter: (value: number) =>
          `${value.toFixed(2)} %`,
      },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;

      if (width) {
        chart.applyOptions({
          width: Math.floor(width),
        });
      }
    });

    observer.observe(container);

    return () => {
      observer.disconnect();
      chart.remove();

      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Aktualizace dat bez opětovného vytváření grafu.
  useEffect(() => {
    const series = seriesRef.current;

    if (!series || !data.length) return;

    const candles = data
      .filter(
        (candle) =>
          Number.isFinite(candle.timestamp) &&
          Number.isFinite(candle.open) &&
          Number.isFinite(candle.high) &&
          Number.isFinite(candle.low) &&
          Number.isFinite(candle.close)
      )
      .map((candle) => ({
        time: candle.timestamp as UTCTimestamp,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      }))
      .sort((a, b) => a.time - b.time);

    // Odstranění případných duplicitních timestampů.
    const uniqueCandles = [
      ...new Map(
        candles.map((candle) => [candle.time, candle])
      ).values(),
    ];

    if (!uniqueCandles.length) return;

    series.setData(uniqueCandles);

    const lastIndex = uniqueCandles.length - 1;
    const from = Math.max(0, lastIndex - visibleCandles + 1);

    chartRef.current?.timeScale().setVisibleLogicalRange({
      from,
      to: lastIndex,
    });
  }, [data, visibleCandles]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: 320,
      }}
    />
  );
}