import { Suspense } from "react";
import { HomeClient } from "@/components/home-client";

export default function HomePage() {
  return (
    <Suspense fallback={<main><p className="muted">Loading…</p></main>}>
      <HomeClient />
    </Suspense>
  );
}
