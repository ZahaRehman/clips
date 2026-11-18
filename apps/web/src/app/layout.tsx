import type { Metadata } from "next";
import localFont from "next/font/local";
import { Suspense } from "react";
import { AppShell } from "@/components/app-shell";
import "./globals.css";

// Bundled in the repo so `next build` never needs Google Fonts (Docker builds were timing out on it).
const sans = localFont({
  src: [{ path: "./fonts/plex-sans.woff2", weight: "400 600", style: "normal" }],
  variable: "--font-sans",
});

const mono = localFont({
  src: [
    { path: "./fonts/plex-mono-400.woff2", weight: "400", style: "normal" },
    { path: "./fonts/plex-mono-500.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "Cliping",
  description: "Local long-form to short-form clipping",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body>
        <Suspense fallback={<div className="canvas">{children}</div>}>
          <AppShell>{children}</AppShell>
        </Suspense>
      </body>
    </html>
  );
}
