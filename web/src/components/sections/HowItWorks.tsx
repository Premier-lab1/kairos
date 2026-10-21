import { Section, SectionHeading } from "@/components/ui/Section";
import { Reveal } from "@/components/ui/Reveal";
import { Pipeline } from "@/components/how/Pipeline";

export function HowItWorks() {
  return (
    <Section id="how" tone="light">
      <SectionHeading
        tone="light"
        index="01"
        kicker="How it works"
        title={
          <>
            How it <em>works</em>
          </>
        }
        lead="Kairos doesn't ask “what pumped”. It asks “who already called this, and how did their past calls end”."
      />
      <Pipeline />
      <Reveal className="mt-14 sm:mt-20">
        <p className="max-w-[64ch] border-l-2 border-accent pl-4 font-mono text-xs leading-relaxed text-ink/70 sm:text-[0.8rem]">
          Scoring is rules and statistics over the database. AI only explains strong signals; it is not the source of the prediction.
        </p>
      </Reveal>
    </Section>
  );
}
