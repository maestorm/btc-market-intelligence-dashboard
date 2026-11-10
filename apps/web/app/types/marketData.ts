export type MarketData = {
  updatedAt: string;
  btcUsd: number | null;
  dvol: number | null;
  realizedVol30dPct: number | null;
  fundingRatePct: number | null;
  openInterestUsd: number | null;
  dvolHistory: DvolCandle[];
  fearGreed?: {
    value: number | null;
    classification: string | null;
    updatedAt: string | null;
} | null;
};

export type DvolCandle = {
  time: string;
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  value: number;
};