import type { Metadata, Viewport } from "next";
import { Inter_Tight, Geist_Mono } from "next/font/google";
import "./globals.css";

const interTight = Inter_Tight({
  variable: "--font-inter-tight",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Kairos — early signals for Pump.fun tokens",
  description:
    "Kairos matches a new token against shiller history: who is already in, at which minute, and how similar cases ended. While the trench is still forming.",
};

export const viewport: Viewport = {
  themeColor: "#0a0a0a",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${interTight.variable} ${geistMono.variable} antialiased`}>
      <body className="min-h-dvh bg-ink text-fg">{children}</body>
    </html>
  );
}
