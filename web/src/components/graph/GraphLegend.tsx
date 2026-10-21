import { caTierColor, outcomeColor, outcomeLabel, type Outcome } from "@/lib/demo";

export function GraphMetricsLegend() {
  return (
    <p className="font-mono text-[0.65rem] leading-relaxed text-muted">
      SMARTS — smart followers · CA — memecoins called (
      <span style={{ color: caTierColor.low }}>green &lt; 10</span>,{" "}
      <span style={{ color: caTierColor.mid }}>yellow 10–19</span>,{" "}
      <span style={{ color: caTierColor.high }}>red ≥ 20</span>)
    </p>
  );
}

export function GraphArticleLegend() {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap justify-center gap-x-5 gap-y-1 font-mono text-[0.65rem] text-muted">
        <span>Large node — shiller · small dots — past calls · line — called this token</span>
        {(["success", "rug", "dead", "neutral"] as Outcome[]).map((outcome) => (
          <span key={outcome} className="inline-flex items-center gap-1.5">
            <span className="size-1.5 rounded-full" style={{ background: outcomeColor[outcome] }} aria-hidden />
            {outcomeLabel[outcome]}
          </span>
        ))}
      </div>
      <GraphMetricsLegend />
    </div>
  );
}
