"use client";

import { useEffect, useState } from "react";
import { disconnectYouTube, youtubeConnectHref, youtubeStatus, type YouTubeStatus } from "@/lib/api";

export function YouTubeConnect() {
  const [status, setStatus] = useState<YouTubeStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const flag = new URLSearchParams(window.location.search).get("youtube");
    if (flag === "error") setNote("YouTube connect did not finish. Try again.");
    if (flag === "denied") setNote("YouTube connect was cancelled.");
    void youtubeStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  function connect() {
    const next = window.location.pathname.startsWith("/projects/") ? window.location.pathname : "/";
    window.location.href = youtubeConnectHref(next);
  }

  async function disconnect() {
    if (!window.confirm("Disconnect YouTube? Videos already posted stay on the channel.")) return;
    setBusy(true);
    setNote(null);
    try {
      await disconnectYouTube();
      setStatus(await youtubeStatus());
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Disconnect failed");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  if (!status.configured) {
    return <span className="pill muted">YouTube not configured</span>;
  }
  if (!status.connected) {
    return (
      <span className="pill">
        <button type="button" onClick={connect} disabled={busy}>
          Connect YouTube
        </button>
        {note && <span className="bad small">{note}</span>}
      </span>
    );
  }
  return (
    <span className="pill">
      <span>{status.channel_label || "YouTube connected"}</span>
      <button className="secondary" type="button" onClick={() => void disconnect()} disabled={busy}>
        Disconnect
      </button>
      {note && <span className="bad small">{note}</span>}
    </span>
  );
}
