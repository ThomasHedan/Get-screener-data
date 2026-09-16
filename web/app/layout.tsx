import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Warrior Screener",
  description: "Low-float momentum names in play, rescanned through the session.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
