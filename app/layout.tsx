import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Warrior Trading Screener",
  description:
    "The day's in-play low-float momentum stocks: up 10%+, $1–$20, 5x relative volume, float under 10M.",
};

export const viewport: Viewport = {
  themeColor: "#0b0e14",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
