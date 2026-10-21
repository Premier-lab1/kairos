import Link from "next/link";
import { requireUser } from "@/lib/server/auth";
import { getSignals } from "@/lib/server/queries/signals";
import { Card, PageTitle } from "@/components/dash/ui";
import { SignalsTable, linkClass } from "@/components/dash/core/DataViews";

export default async function SignalsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  await requireUser();
  const params = await searchParams;
  const parsed = typeof params.min === "string" ? Number(params.min) : 0;
  const min = Number.isFinite(parsed) ? Math.min(100, Math.max(0, parsed)) : 0;
  const confidence = typeof params.confidence === "string" && ["low", "medium", "high"].includes(params.confidence) ? params.confidence : null;
  const signals = await getSignals({ min, confidence });
  const href = (score: number, conf: string | null) => `/app/signals?${new URLSearchParams({ min: String(score), ...(conf ? { confidence: conf } : {}) })}`;
  return <><PageTitle title="Signals" sub="Scorer s1 · up to 100 signals, most recently updated first. Confidence describes the available evidence." />
    <Card title="Filters"><nav aria-label="Signal filters" className="space-y-3 font-mono text-xs"><div className="flex flex-wrap gap-4"><span className="text-muted">Score</span>{[0, 60].map(v => <Link key={v} className={linkClass} href={href(v, confidence)} aria-current={min === v ? "page" : undefined}>{v ? "60+" : "All"}{min === v ? " ✓" : ""}</Link>)}{![0, 60].includes(min) && <span>{min}+</span>}</div><div className="flex flex-wrap gap-4"><span className="text-muted">Confidence</span>{[null, "low", "medium", "high"].map(v => <Link key={v ?? "all"} className={linkClass} href={href(min, v)} aria-current={confidence === v ? "page" : undefined}>{v ?? "All"}{confidence === v ? " ✓" : ""}</Link>)}</div></nav></Card>
    <div className="mt-5"><Card title="Signals"><SignalsTable signals={signals} /></Card></div></>;
}
