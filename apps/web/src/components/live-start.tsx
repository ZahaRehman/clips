"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { createLiveProject } from "@/lib/api";

type Props = {
  disclaimer: boolean;
  autopilot: boolean;
  clipCount: number;
  framing: "face" | "gameplay" | "full";
  uploading: boolean;
  onBusy: (value: boolean) => void;
  onError: (message: string | null) => void;
};

export function LiveStart({ disclaimer, autopilot, clipCount, framing, uploading, onBusy, onError }: Props) {
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState("");

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!disclaimer) {
      onError("Accept the rights disclaimer before starting a live capture.");
      return;
    }
    const liveUrl = url.trim();
    const liveFolder = folder.trim();
    if (Boolean(liveUrl) === Boolean(liveFolder)) {
      onError("Enter a live URL or a folder, not both.");
      return;
    }
    onBusy(true);
    onError(null);
    try {
      const created = await createLiveProject({
        url: liveUrl || undefined,
        folder: liveFolder || undefined,
        disclaimer_accepted: true,
        autopilot,
        clip_count: autopilot ? clipCount : undefined,
        framing,
      });
      window.location.href = `/projects/${created.id}`;
    } catch (err) {
      onError(err instanceof Error ? err.message : "Live capture failed");
    } finally {
      onBusy(false);
    }
  }

  return (
    <section className="card" style={{ marginBottom: "1.25rem" }}>
      <h2>Live</h2>
      <p className="muted">
        Clips show up while the stream continues, about one chunk behind. The rights checkbox above applies.
      </p>
      <p>
        <Link href="/live">Browse streams that are live now</Link>
      </p>
      <form className="stack" onSubmit={(event) => void onSubmit(event)}>
        <input
          type="url"
          placeholder="YouTube live URL"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          disabled={uploading}
        />
        <input
          type="text"
          placeholder="Or a folder of finished segments"
          value={folder}
          onChange={(event) => setFolder(event.target.value)}
          disabled={uploading}
        />
        <button type="submit" disabled={uploading || !disclaimer}>
          Start live capture
        </button>
      </form>
    </section>
  );
}
