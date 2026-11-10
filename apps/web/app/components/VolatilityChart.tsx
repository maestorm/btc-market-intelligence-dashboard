"use client";

import { useEffect, useRef } from "react";
import {
  createChart,
  LineSeries,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

type Point = {
  time: string;
  value: number;
};

export default function VolatilityChart({ data }: { data: Point[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const didFitContentRef = useRef(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 280,

      localization: {
        timeFormatter: (timestamp: number) => {
          return new Date(timestamp * 1000).toLocaleString("cs-CZ", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            timeZone: "Europe/Prague", //timeZone: "UTC",
          });
        },
      },

      layout: {
        background: { color: "#ffffff" },
        textColor: "#475569",
      },

      grid: {
        vertLines: { color: "#eef2f7" },
        horzLines: { color: "#eef2f7" },
      },
    });

    seriesRef.current = chart.addSeries(LineSeries, {
      color: "#2563eb",
      lineWidth: 2,
      title: "DVOL",
    });

    const observer = new ResizeObserver(([entry]) => {
      chart.applyOptions({ width: entry.contentRect.width });
    });
    observer.observe(container);

    return () => {
      observer.disconnect();
      seriesRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const points = data
      .map((point) => ({
        time: Math.floor(
          new Date(point.time).getTime() / 1000
        ) as UTCTimestamp,
        value: point.value,
      }))
      .filter(
        (point) =>
          Number.isFinite(point.time) && Number.isFinite(point.value)
      )
      .sort((a, b) => a.time - b.time);

    series.setData(points);

    if (points.length > 0 && !didFitContentRef.current) {
      series.priceScale().applyOptions({ autoScale: true });
      didFitContentRef.current = true;
    }
  }, [data]);

  return <div ref={containerRef} />;
}
