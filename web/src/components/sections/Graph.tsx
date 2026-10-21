import { Section, SectionHeading } from "@/components/ui/Section";
import { Reveal } from "@/components/ui/Reveal";
import { GraphExplorer } from "@/components/graph/GraphExplorer";

export function Graph() {
  return (
    <Section id="graph" tone="dark">
      <SectionHeading
        index="04"
        kicker="Shiller database"
        title={
          <>
            Shiller → token <em>graph</em>
          </>
        }
        lead="Every shiller leaves a trail: which tokens they called and how those ended. The database grows every day, and it can't be rebuilt in a week."
      />
      <Reveal>
        <GraphExplorer />
      </Reveal>
    </Section>
  );
}
