"use client";

import type { ChunkStatus } from "@/lib/api";

export function ChunkBoard({ chunks }: { chunks: ChunkStatus }) {
  return (
    <section className="card" style={{ marginBottom: "1rem" }}>
      <h2>Chunks</h2>
      <p className="subtitle">
        {chunks.saved} saved · {chunks.ready} ready
        {chunks.failed > 0 ? ` · ${chunks.failed} failed` : ""} · {chunks.total} planned
      </p>
      <p className="muted">
        This finished video is fetched in 5-minute pieces. Shorts from the first piece can show up while later pieces
        are still downloading.
      </p>
      {chunks.fetching > 0 && <p className="muted">Downloading the next piece.</p>}
      {chunks.error && <p className="bad">{chunks.error}</p>}
    </section>
  );
}
