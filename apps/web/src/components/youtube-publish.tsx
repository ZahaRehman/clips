"use client";

import { useEffect, useMemo, useState } from "react";
import type { PublishRequest, PublishRow, YouTubeStatus } from "@/lib/api";

export type PublishItem = {
  id: string;
  title: string;
  description: string;
  duration: number;
  picked: boolean;
  selectable: boolean;
  dirty: boolean;
};

type Props = {
  status: YouTubeStatus;
  items: PublishItem[];
  publishes: PublishRow[];
  busy: boolean;
  onPost: (body: PublishRequest) => Promise<void>;
};

const ACTIVE = new Set(["queued", "uploading", "processing"]);

export function PublishBar({ status, items, publishes, busy, onPost }: Props) {
  const [mode, setMode] = useState<"spaced" | "now">("spaced");
  const [privacy, setPrivacy] = useState<"public" | "unlisted" | "private">("private");
  const [notify, setNotify] = useState(false);
  const [kids, setKids] = useState<boolean | null>(status.made_for_kids ?? null);
  const [rights, setRights] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (kids === null && (status.made_for_kids === true || status.made_for_kids === false)) {
      setKids(status.made_for_kids);
    }
  }, [kids, status.made_for_kids]);

  const selected = items.filter((item) => item.picked && item.selectable);
  const dirty = selected.some((item) => item.dirty);
  const running = publishes.some((row) => ACTIVE.has(row.status));
  const spacing = status.default_spacing_min || 180;
  const preview = useMemo(() => previewRows(selected, mode, privacy, spacing), [selected, mode, privacy, spacing]);

  function connect() {
    const next = window.location.pathname.startsWith("/projects/") ? window.location.pathname : "/";
    window.location.href = `/api/backend/api/v1/youtube/connect?next=${encodeURIComponent(next)}`;
  }

  async function send() {
    if (kids === null) return;
    setError(null);
    const body: PublishRequest = {
      clip_ids: selected.map((item) => item.id),
      privacy: mode === "spaced" ? "public" : privacy,
      notify_subscribers: notify,
      made_for_kids: kids,
      rights_confirmed: true,
    };
    if (mode === "spaced") {
      body.schedule = { start_at: new Date().toISOString(), every_minutes: spacing };
    }
    try {
      await onPost(body);
      setOpen(false);
      setRights(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Post failed");
    }
  }

  const blocked = busy || running || dirty || selected.length === 0 || kids === null || !rights;

  return (
    <div className="publish-bar">
      <div className="publish-grid">
        <label className="field">
          Schedule
          <select value={mode} onChange={(e) => setMode(e.target.value === "now" ? "now" : "spaced")}>
            <option value="spaced">Spaced: first now, then every {spacing / 60} hours</option>
            <option value="now">All now</option>
          </select>
        </label>
        {mode === "now" && (
          <label className="field">
            Visibility
            <select value={privacy} onChange={(e) => setPrivacy(e.target.value as typeof privacy)}>
              <option value="private">Private</option>
              <option value="unlisted">Unlisted</option>
              <option value="public">Public</option>
            </select>
          </label>
        )}
        <label className="field">
          Made for kids
          <select
            value={kids === null ? "" : kids ? "yes" : "no"}
            onChange={(e) => setKids(e.target.value === "" ? null : e.target.value === "yes")}
          >
            <option value="">Choose</option>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </label>
      </div>
      <label className="check">
        <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />
        <span>Notify subscribers</span>
      </label>
      <label className="check">
        <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} />
        <span>I own these videos or have permission to post them.</span>
      </label>
      {dirty && <p className="banner warn">Save title and description on the selected clips first.</p>}
      {running && <p className="muted small">A batch is uploading. This button stays off until it finishes.</p>}
      {error && <p className="bad small">{error}</p>}
      <div className="actions">
        {status.connected ? (
          <button type="button" disabled={blocked} onClick={() => setOpen(true)}>
            Post {selected.length} clip{selected.length === 1 ? "" : "s"}
          </button>
        ) : (
          <button type="button" onClick={connect}>
            Connect YouTube
          </button>
        )}
        <span className="muted small">
          {status.connected ? status.channel_label : "Not connected"}
        </span>
      </div>
      {open && (
        <div className="sheet-backdrop" role="presentation" onClick={() => setOpen(false)}>
          <div
            className="sheet"
            role="dialog"
            aria-modal="true"
            aria-labelledby="publish-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="publish-title">Post {selected.length} clips</h2>
            <p>
              Channel: <strong>{status.channel_label || "Connected (channel name unavailable)"}</strong>
            </p>
            <p className="banner">Titles and descriptions are sent exactly as shown. Nothing is added after you post.</p>
            <ul className="confirm-list">
              {preview.map((row) => (
                <li key={row.id}>
                  <strong>{row.title}</strong>
                  <span className="muted">{row.whenLabel}</span>
                  {row.over60 && <span className="badge warn">Over 60s</span>}
                  <pre>{row.description || "(empty description)"}</pre>
                </li>
              ))}
            </ul>
            <div className="actions">
              <button type="button" disabled={busy} onClick={() => void send()}>
                Post {selected.length} clips
              </button>
              <button className="secondary" type="button" onClick={() => setOpen(false)}>
                Back
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function previewRows(
  items: PublishItem[],
  mode: "spaced" | "now",
  privacy: "public" | "unlisted" | "private",
  spacingMin: number,
) {
  const start = Date.now();
  return items.map((item, index) => {
    let whenLabel: string;
    if (mode === "now") {
      whenLabel = `${label(privacy)} now`;
    } else {
      const when = start + index * spacingMin * 60_000;
      whenLabel = when <= start + 5_000 ? "Public now" : `Private until ${new Date(when).toLocaleString()}, then public`;
    }
    return {
      id: item.id,
      title: item.title,
      description: item.description,
      whenLabel,
      over60: item.duration > 60,
    };
  });
}

function label(privacy: "public" | "unlisted" | "private"): string {
  if (privacy === "public") return "Public";
  if (privacy === "unlisted") return "Unlisted";
  return "Private";
}
