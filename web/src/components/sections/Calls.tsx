import { CallChart } from "@/components/chart/CallChart";
import { Avatar } from "@/components/ui/Avatar";
import { DemoTag } from "@/components/ui/DemoTag";
import { Section, SectionHeading } from "@/components/ui/Section";
import { demoSignal } from "@/lib/demo";
import { demoChart } from "@/lib/demoChart";

export function Calls() {
  const start = demoChart.points[0].t;
  const first = Math.min(...demoChart.calls.map(call => call.t));
  const peak = Math.max(...demoChart.points.map(point => point.mcapSol));
  const signalCap = demoChart.points.find(point => point.t === demoChart.marker?.t)!.mcapSol;
  const stats = [
    ["First call", `minute ${(first - start) / 60}`],
    ["Kairos signal", `minute ${((demoChart.marker?.t ?? start) - start) / 60}`],
    ["Peak / signal", `×${(peak / signalCap).toFixed(1)}`],
  ];
  return (
    <Section id="calls">
      <SectionHeading index="03" kicker="Calls on the chart" title={<>Watch the calls <em>hit the chart</em></>} lead="Every avatar is a shiller posting the CA. Kairos sees who is in and when — and checks how their past calls ended — before the crowd does." />
      <div className="min-w-0 rounded-2xl border border-line bg-ink-2/50 p-2 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 px-2 pt-2 pb-5">
          <div><p className="font-mono text-sm text-fg">{demoSignal.ticker} <span className="text-muted">/ SOL</span></p><p className="mt-1 font-mono text-[10px] text-muted">PUMP.FUN · FIRST 60 MINUTES</p></div>
          <DemoTag />
        </div>
        <CallChart {...demoChart} playOnView ariaLabel="Fictional HALCYON market cap over 60 minutes, with shiller calls at minutes 3, 5, 7, 12 and 18. Kairos signals at 84 SOL in minute 7; the market cap peaks near 438 SOL." />
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3 px-2 py-5 font-mono text-[10px] text-muted">
          <span className="inline-flex items-center gap-2"><Avatar username={demoChart.calls[0].username} size={18} remote={false} /> Call</span>
          <span className="inline-flex items-center gap-2"><span className="h-px w-5 bg-accent" /> Market cap</span>
          <span className="inline-flex items-center gap-2"><span className="flex items-end gap-0.5"><i className="h-3 w-1 bg-win/50" /><i className="h-2 w-1 bg-rug/50" /></span> Buys / sells</span>
          <span className="inline-flex items-center gap-2"><span className="w-5 border-t border-dashed border-accent" /> Kairos signal</span>
        </div>
        <dl className="grid grid-cols-3 divide-x divide-line border-t border-line py-4 font-mono">
          {stats.map(([label, value]) => <div key={label} className="px-2 sm:px-5"><dt className="text-[9px] text-muted sm:text-xs">{label}</dt><dd className="mt-2 text-xs text-fg sm:text-lg">{value}</dd></div>)}
        </dl>
      </div>
    </Section>
  );
}
