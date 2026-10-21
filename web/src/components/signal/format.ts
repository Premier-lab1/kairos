export function fmtFollowers(n: number) {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

export function pluralCalls(n: number) {
  return n === 1 ? "call" : "calls";
}

export const pct = (v: number) => Math.round(v * 100);
