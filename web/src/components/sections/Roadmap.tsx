import { Section, SectionHeading } from "@/components/ui/Section";
import { Reveal } from "@/components/ui/Reveal";

const stages = [
  { status: "Done", date: "October 2026", items: [
    "Collecting new Pump.fun tokens (PumpPortal)",
    "CA and shiller lookup via Frontrun",
    "Trade backfill (Helius)",
    "Token outcome labels v1: win / rug / dead",
    "Shiller ranking by track record",
  ] },
  { status: "In progress", date: null, items: [
    "Scoring and signals: rules and stats over the shiller database",
  ] },
  { status: "Next", date: null, items: [
    "Telegram bot: closed beta",
    "Web dashboard",
    "Sign-in and subscription",
    "AI explanations for strong signals",
    "Outcome rules v2",
  ] },
];

export function Roadmap() {
  return (
    <Section id="roadmap" tone="light">
      <SectionHeading index="06" kicker="Roadmap" tone="light"
        title={<>What’s done <em>and what’s next</em></>}
        lead="Token history is already being collected. Scoring is in progress. Signal delivery comes next." />
      <ol className="grid gap-10 lg:grid-cols-3 lg:gap-8">
        {stages.map((stage, index) => (
          <Reveal as="li" key={stage.status} delay={index * 120} className="border-t border-paper-line pt-6">
            <div className="mb-8 flex items-center justify-between gap-3">
              <h3 className={`mono-label rounded-full border px-3 py-1.5 ${index === 1 ? "border-accent text-ink" : "border-paper-line text-ink/70"}`}>{stage.status}</h3>
              <span aria-hidden="true" className="font-mono text-5xl font-light text-ink/25">0{index + 1}</span>
            </div>
            {stage.date && <p className="mono-label mb-4 text-ink/60">{stage.date}</p>}
            <ul className="divide-y divide-paper-line border-b border-paper-line">
              {stage.items.map((item) => <li key={item} className="py-4 text-lg leading-snug first:pt-0">{item}</li>)}
            </ul>
          </Reveal>
        ))}
      </ol>
    </Section>
  );
}
