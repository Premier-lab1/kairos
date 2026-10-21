import { Section, SectionHeading } from "@/components/ui/Section";
import { Reveal } from "@/components/ui/Reveal";
import { Phone } from "@/components/telegram/Phone";

const features = [
  ["Straight to chat", "A push within seconds of the shill being found."],
  ["Your threshold", "Set your own confidence threshold and signal filters."],
  ["The short why", "“Why?” gives an AI explanation for strong signals. The score itself comes from rules and stats."],
  ["Less noise", "Mute the shillers you don't trust."],
];

export function Telegram() {
  return (
    <Section id="telegram" className="pb-10">
      <SectionHeading index="05" kicker="Telegram bot"
        title={<>Signals in Telegram <em>in minutes, not hours</em></>}
        lead="On Pump.fun, decisions take minutes. That is why the main channel is a bot, not a dashboard." />
      <div className="grid items-start gap-12 lg:grid-cols-2 lg:gap-24">
        <div>
          <p className="mono-label mb-6 text-muted">In the closed beta</p>
          <ul className="border-t border-line">
            {features.map(([title, copy], index) => (
              <Reveal as="li" key={title} delay={index * 100} className="border-b border-line py-6">
                <h3 className="mb-2 text-xl font-medium">{title}</h3>
                <p className="max-w-md leading-relaxed text-muted">{copy}</p>
              </Reveal>
            ))}
          </ul>
        </div>
        <Phone />
      </div>
    </Section>
  );
}
