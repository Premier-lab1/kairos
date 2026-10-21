import { requireUser } from "@/lib/server/auth";
import { Card, PageTitle, TableWrap, th, td } from "@/components/dash/ui";

const steps = [
  ["PumpPortal", "Discover new tokens and migrations on Solana."],
  ["Frontrun", "Search each contract address (CA) to find who posted it, then collect those shillers’ CA histories."],
  ["Helius", "Backfill on-chain trades to reconstruct market-cap and liquidity history."],
  ["Outcome labelling", "Evaluate token outcomes at 1h, 24h and 7d under versioned rules."],
  ["Scoring", "Every minute, update eligible tokens with new mentions using rules and historical statistics."],
  ["Telegram bot · coming at launch", "Deliver signals above the user’s score threshold, with medium or high confidence only."],
];

const labels = [
  ["pending", "The horizon has not ended. This takes priority even when price data is missing."],
  ["no_data", "The horizon has ended, but usable price data is missing."],
  ["rug", "Any of: liquidity falls strictly more than 80% within ≤10 minutes; the creator sells more than 50% of their tokens during the first hour from the reference; end multiple <0.1 with max drawdown >0.9; or either additional v2 rule below."],
  ["success", "Peak multiple ≥3 AND liquidity is alive AND end multiple ≥1. A rug rule takes priority."],
  ["dead", "No migration by the horizon’s end AND end multiple <0.5, after ruling out rug and success."],
  ["neutral", "All other completed outcomes with sufficient data."],
];

const components = [
  ["shillers", "+55 max", "55 × clamp((best lift − 1) / 4, 0, 1). Only shillers with ≥5 finals qualify; 5× base earns the maximum."],
  ["breadth", "+20 max", "20 × min(shiller count − 1, 4) / 4. One shiller earns 0; five or more earn 20."],
  ["creator_clean", "+10", "Creator has ≥3 past final tokens and no rugs."],
  ["entry", "0", "Shown for context. Early calls can also be bundled or insider launches; direction needs backtesting."],
  ["similar", "0", "Similar past cases are shown only; currently they largely duplicate the shiller evidence."],
  ["rug_shillers", "−30 max penalty", "−30 × clamp(max rug lift − 1, 0, 1), using shillers with ≥5 finals. 2× base rug rate earns the full penalty."],
  ["creator_rug", "−15 / −25", "−15 for ≥1 past rug; −25 if ≥2 past rugs or rugs / finals ≥0.5 (with at least one rug)."],
  ["similar_rug", "0", "Disabled until enough independent evidence exists."],
];

export default async function LogicPage() {
  await requireUser();
  return <>
    <PageTitle title="How Kairos works" sub="A guide for clients and testers. Outcome rules v2 and scorer s1 are working rules, not a validated promise of performance." />
    <div className="space-y-5 text-sm leading-relaxed">
      <Card title="01 · From discovery to signal">
        <ol className="grid gap-3 md:grid-cols-3">{steps.map(([title, description], i) => <li key={title} className="min-w-0 rounded-xl border border-line p-4"><span className="font-mono text-xs text-accent">{String(i + 1).padStart(2, "0")}{i < steps.length - 1 ? " →" : ""}</span><h3 className="mt-2 font-semibold">{title}</h3><p className="mt-1 text-muted">{description}</p></li>)}</ol>
        <p className="mt-4 text-muted">A token is eligible while its first CA mention is at most 6 hours old. Scoring runs every 60 seconds for eligible tokens with new mentions since their last score. The LLM only explains signals above the threshold; it does not decide the score.</p>
      </Card>
      <Card title="02 · What an outcome means">
        <p className="mb-4 text-muted">Each token can have separate outcomes for <span className="font-mono text-fg">1h / 24h / 7d</span> and two references. <strong className="text-fg">Creation</strong> describes the token’s life from launch and supports creator history. <strong className="text-fg">First mention</strong> starts at anyone’s earliest CA call: it asks what happened from the point a shiller drew attention to it. This is the dashboard default and the basis for shiller statistics.</p>
        <TableWrap><table className="w-full min-w-[480px]"><caption className="sr-only">Outcome v2 labels in evaluation order</caption><thead><tr><th className={th} scope="col">Label</th><th className={th} scope="col">Exact rule · first match wins</th></tr></thead><tbody>{labels.map(([label, rule]) => <tr key={label}><th scope="row" className={`${td} text-left font-normal text-accent`}>{label}</th><td className="border-b border-line/60 px-3 py-3 text-sm">{rule}</td></tr>)}</tbody></table></TableWrap>
        <dl className="mt-5 space-y-4">
          <div><dt className="font-mono text-accent">v2 · fast dump</dt><dd className="mt-1 text-muted">Any two market-cap observations inside the horizon with <span className="font-mono">0 &lt; elapsed ≤30 minutes</span> and <span className="font-mono">1 − later cap / earlier cap &gt;0.9</span>. Exactly 90% is not enough; exactly 30 minutes is included.</dd></div>
          <div><dt className="font-mono text-accent">v2 · pump-and-dump</dt><dd className="mt-1 text-muted">The global peak is ≥3× reference cap, then a later observation within 30 minutes of that peak has cap ≤ reference + 0.1 × (peak − reference). At least 90% of the gain was surrendered. The 3×, 90% and 30-minute boundaries are included; observations stay inside the horizon.</dd></div>
          <div><dt className="font-mono">Measurement details</dt><dd className="mt-1 text-muted">All multiples use market cap in SOL. Reference cap is the last known cap at or before the reference time, or the first within 2 minutes after it. Peak multiple = peak / reference; end multiple = end / reference. Max drawdown = 1 − lowest cap after the peak / peak. Liquidity is alive when ending liquidity is ≥50% of its peak and ≥5 SOL. Liquidity means real SOL in the curve or pool, excluding the curve’s virtual 30 SOL.</dd></div>
        </dl>
        <p className="mt-4 text-muted">Creator-sale rules are conservative: when holdings are unknown (sales exceed observed purchases), that rule does not fire. Sales through related wallets are not captured. Final labels are success, rug, dead and neutral; pending and no_data never enter win or rug rates. Version v1 remains stored for comparison; v2 adds the two dump rules above.</p>
      </Card>
      <Card title="03 · Scoring s1">
        <h3 className="font-semibold">No future leakage</h3>
        <p className="mt-2 text-muted">At scoring time t, use only mentions with tweet time ≤t and past final outcomes whose reference time + horizon ≤t. Exclude the token being scored from its own history. Recompute base rates as of t. Backtests apply the same rules at first mention + a delay (10 minutes by default). Shiller history uses v2, 24h, first mention; creator history uses v2, 24h, creation and earlier tokens.</p>
        <p className="mt-3 text-muted">A past token counts for a shiller only if that shiller’s first mention was within 60 minutes of the token’s first mention by anyone. Each token counts once. Late calls must not receive credit for growth that already happened.</p>
        <h3 className="mt-5 font-semibold">Base rates and shrinkage</h3>
        <p className="mt-2 text-muted">Base b is the success share among all eligible final first-mention outcomes; b_rug is the rug share. With fewer than 100 finals, use <span className="font-mono text-fg">b = 0.05</span> and <span className="font-mono text-fg">b_rug = 0.30</span>. Shrink each shiller toward that base with <span className="font-mono text-fg">k = 10</span>:</p>
        <div className="my-4 space-y-2 rounded-lg border border-line bg-ink p-3 font-mono text-xs [overflow-wrap:anywhere]">
          <p>p_shr = (wins + 10 × b) / (finals + 10)</p><p>lift = p_shr / b</p>
          <p>rug_shr = (rugs + 10 × b_rug) / (finals + 10)</p><p>rug_lift = rug_shr / b_rug</p>
        </div>
        <p className="mb-4 text-muted">A newcomer starts at lift 1 and earns no history bonus. The 95% Wilson lower bound is displayed as a conservative statistical summary; it does not contribute points.</p>
        <TableWrap><table className="w-full min-w-[560px]"><caption className="sr-only">Scorer s1 components, weights and caps</caption><thead><tr>{["Component", "Weight / cap", "Rule"].map(label => <th key={label} className={th} scope="col">{label}</th>)}</tr></thead><tbody>{components.map(([name, weight, rule]) => <tr key={name}><th scope="row" className={`${td} text-left font-normal`}>{name}</th><td className={td}>{weight}</td><td className="border-b border-line/60 px-3 py-3 text-sm">{rule}</td></tr>)}</tbody></table></TableWrap>
        <p className="mt-3 text-muted">Clamp limits a value to the stated range. Positive components total at most 85 points; the final sum is clamped to 0–100.</p>
        <h3 className="mt-5 font-semibold">Confidence measures evidence, not token quality</h3>
        <p className="mt-2 text-muted">Choose the highest-lift shiller among those with ≥5 finals. Confidence is:</p>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-muted"><li><span className="font-mono text-fg">high</span>: that shiller has ≥30 finals, and at least one other shiller on this token has ≥10 finals.</li><li><span className="font-mono text-fg">medium</span>: that shiller has ≥10 finals, but the high rule is not met.</li><li><span className="font-mono text-fg">low</span>: otherwise, including no qualifying shiller.</li></ul>
        <p className="mt-4 text-muted">The default strong-signal threshold is <span className="font-mono text-fg">60</span>, a placeholder until backtesting. The launch bot will send only medium/high-confidence signals meeting the user’s threshold (default 60). Weights and thresholds need calibration on a larger history.</p>
      </Card>
      <Card title="04 · Current limits">
        <ul className="list-disc space-y-3 pl-5 text-muted">
          <li><strong className="text-fg">Little history.</strong> Most outcomes are still pending, so live scores have little evidence and are often low-confidence. A high win rate from a tiny sample is not a reliable track record.</li>
          <li><strong className="text-fg">Daily API budgets.</strong> Frontrun: <span className="font-mono">3,000 credits</span>; Helius: <span className="font-mono">20,000 credits</span>. Requests are paced through the UTC day, so collection and backfills are not instant.</li>
          <li><strong className="text-fg">Creation time can be wrong.</strong> For tokens first seen at migration, created_at may actually be the migration time. Entry minutes can therefore be unknown or misleading. Shiller tables suppress missing, negative and obviously migration-based ages (creation at or within one second before migration, or later); this cannot identify every bad timestamp.</li>
          <li><strong className="text-fg">Search latency.</strong> Frontrun finds posts after their tweet time. Backtests using tweet timestamps can look more timely than live signals. Collection delay must be measured before judging real-world performance.</li>
        </ul>
      </Card>
      <p className="pb-6 font-mono text-xs text-muted">Not financial advice.</p>
    </div>
  </>;
}
