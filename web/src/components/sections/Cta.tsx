import { Section } from "@/components/ui/Section";
import { Button } from "@/components/ui/Button";
import { Reveal } from "@/components/ui/Reveal";

export function Cta() {
  return (
    <Section id="cta">
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 opacity-20"
        style={{ backgroundImage: "radial-gradient(var(--color-accent) 1px, transparent 1px)", backgroundSize: "24px 24px", maskImage: "linear-gradient(to right, transparent 20%, black)" }} />
      <Reveal className="relative">
        <p className="mono-label mb-8 text-muted">Next step · open dashboard</p>
        <h2 className="headline max-w-[14ch]">See the trench <em>before the crowd</em></h2>
        <p className="mt-8 max-w-md text-lg text-muted">The dashboard is open: live signals, shiller history and token pages, no sign-up. The Telegram bot follows in closed beta.</p>
        <div className="mt-10 flex flex-wrap gap-3">
          <Button href="/app">Open dashboard</Button>
          <Button href="#how" variant="ghost">How it works</Button>
        </div>
      </Reveal>
    </Section>
  );
}
