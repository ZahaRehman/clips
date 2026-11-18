"use client";

import { useEffect, useState } from "react";
import { disconnectTikTok, tiktokConnectHref, tiktokStatus, type TikTokStatus } from "@/lib/api";

export function TikTokConnect() {
  const [status, setStatus] = useState<TikTokStatus | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const flag = new URLSearchParams(window.location.search).get("tiktok");
    if (flag === "error") setNote("TikTok connect did not finish. Try again.");
    if (flag === "denied") setNote("TikTok connect was cancelled.");
    void tiktokStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  function connect() {
    const next = window.location.pathname.startsWith("/projects/") ? window.location.pathname : "/";
    window.location.href = tiktokConnectHref(next);
  }

  async function disconnect() {
    if (!window.confirm("Disconnect TikTok? Posts already sent stay on the account.")) return;
    setBusy(true);
    setNote(null);
    try {
      await disconnectTikTok();
      setStatus(await tiktokStatus());
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Disconnect failed");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  if (!status.configured) {
    return <span className="pill muted">TikTok not configured</span>;
  }
  if (!status.connected) {
    return (
      <span className="pill">
        <button type="button" onClick={connect} disabled={busy}>
          Connect TikTok
        </button>
        {note && <span className="bad small">{note}</span>}
      </span>
    );
  }
  return (
    <span className="pill">
      <span>{status.display_name || "TikTok connected"}</span>
      <button className="secondary" type="button" onClick={() => void disconnect()} disabled={busy}>
        Disconnect
      </button>
      {note && <span className="bad small">{note}</span>}
    </span>
  );
}
