export type Step = {
  title: string;
  text: string;
  /** where the data comes from */
  source: string;
};

export const steps: Step[] = [
  {
    title: "New token",
    text: "Every new Pump.fun launch and migration, in real time.",
    source: "PumpPortal",
  },
  {
    title: "Who called it",
    text: "We search for the token's CA. Whoever posted it becomes a shiller in the database.",
    source: "Frontrun",
  },
  {
    title: "Shiller history",
    text: "Every CA they have posted before, deleted posts included.",
    source: "Frontrun",
  },
  {
    title: "How it ended",
    text: "The outcome of each past token after 1h / 24h / 7d: win, rug or dead.",
    source: "Helius · on-chain",
  },
  {
    title: "Signal",
    text: "Who is in, at which minute, what it looks like, how confident. In Telegram within minutes.",
    source: "Kairos",
  },
];
