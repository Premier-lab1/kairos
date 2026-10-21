import { Section, SectionHeading } from "@/components/ui/Section";
import { Reveal } from "@/components/ui/Reveal";
import { CaseFile } from "@/components/signal/CaseFile";

export function SignalCard() {
  return (
    <Section id="signal">
      <SectionHeading
        index="02"
        kicker="Signal card"
        title={
          <>
            The signal <em>card</em>
          </>
        }
        lead="What you get within minutes of the first shill: who is already in, at which minute, what it looks like and how confident the system is."
      />
      <Reveal>
        <CaseFile />
      </Reveal>
    </Section>
  );
}
