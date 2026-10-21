import type { Outcome } from "@/lib/demo";

/**
 * Contract for <CallChart/> (components/chart/CallChart.tsx): a token's market-cap line with
 * buy/sell volume and the moments shillers posted the CA ("calls"), shown as avatars on the line.
 * Used by the landing page (demo data, lib/demoChart.ts) and the dashboard token page (live data).
 * All times are unix seconds (UTC).
 */
export type MarketPoint = { t: number; mcapSol: number };

export type VolumeBar = { t: number; buySol: number; sellSol: number };

export type CallMarker = {
  t: number;
  /** X handle without "@" */
  username: string;
  displayName?: string | null;
  followers?: number | null;
  /** stored profile image (kols.avatar_url); unavatar.io is the fallback when `href` is set */
  avatarUrl?: string | null;
  /** outcome of this shiller's history, to tint the ring (optional) */
  tone?: "good" | "bad" | "neutral";
  /** link for the avatar (e.g. the tweet or the dashboard shiller page) */
  href?: string;
};

/** A dot on the line, e.g. a tracked KOL wallet buying or selling. */
export type ChartEvent = {
  t: number;
  tone: "up" | "down" | "accent";
  /** tooltip text */
  label: string;
};

export type CallChartProps = {
  points: MarketPoint[];
  volume?: VolumeBar[];
  calls: CallMarker[];
  /** px; default 360 (mobile) / 420 (desktop) */
  height?: number;
  /**
   * Landing mode: when the chart scrolls into view, the line "plays" from left to right and
   * avatars pop in as the playhead passes their time. Ignored with prefers-reduced-motion.
   */
  playOnView?: boolean;
  /** vertical marker, e.g. the signal time */
  marker?: { t: number; label: string };
  /** optional final outcome label shown in the corner */
  outcome?: Outcome;
  /**
   * "glow" (dashboard token window): glowing area line coloured by `trend`, a floating value badge
   * on the last point, orange anchor dots under the call avatars and `events` as dots on the line.
   * Default: the classic orange line used on the landing page.
   */
  variant?: "classic" | "glow";
  /** glow colour: up = green, down = red, flat = grey (default up) */
  trend?: "up" | "down" | "flat";
  events?: ChartEvent[];
  ariaLabel: string;
};
