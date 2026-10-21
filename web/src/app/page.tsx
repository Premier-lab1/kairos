import { Header } from "@/components/sections/Header";
import { Hero } from "@/components/sections/Hero";
import { HowItWorks } from "@/components/sections/HowItWorks";
import { SignalCard } from "@/components/sections/SignalCard";
import { Calls } from "@/components/sections/Calls";
import { Graph } from "@/components/sections/Graph";
import { Telegram } from "@/components/sections/Telegram";
import { Roadmap } from "@/components/sections/Roadmap";
import { Cta } from "@/components/sections/Cta";
import { Footer } from "@/components/sections/Footer";

// Order: dark hero → light "how" → dark signal/calls/graph/telegram → light roadmap → dark CTA/footer.
export default function Home() {
  return (
    <>
      <Header />
      <main id="top">
        <Hero />
        <HowItWorks />
        <SignalCard />
        <Calls />
        <Graph />
        <Telegram />
        <Roadmap />
        <Cta />
      </main>
      <Footer />
    </>
  );
}
