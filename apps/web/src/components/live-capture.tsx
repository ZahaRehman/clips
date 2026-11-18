"use client";

import { broadcastLabel, type Clip, type Project } from "@/lib/api";

type Props = {
  project: Project;
  clips: Clip[];
  busy: boolean;
  onStop: () => void;
};

export function LiveCapture({ project, clips, busy, onStop }: Props) {
  if (project.source_type !== "live") return null;
  const live = project.live;
  const message = project.source_meta?.live_message;
  const running = project.status === "live";
  return (
    <section className="card" style={{ marginBottom: "1rem" }}>
      <h2>Live capture</h2>
      {live && (
        <p className="subtitle">
          <span className="badge">{live.mode}</span>
          <span className="badge">{live.state}</span>
          <span>
            {live.windows_saved ?? 0} saved · {live.windows_ready} ready
          </span>
          {live.windows_failed > 0 && <span className="bad"> · {live.windows_failed} failed</span>}
          <span> · about {Math.round(live.behind_sec)}s behind</span>
        </p>
      )}
      {typeof message === "string" && message && <p>{message}</p>}
      {running && (
        <button type="button" disabled={busy || live?.state === "stopping"} onClick={onStop}>
          Stop
        </button>
      )}
      {running && (
        <div className="gallery" style={{ marginTop: "0.75rem" }}>
          {clips.length === 0 ? (
            <p className="muted">{waitingCopy(live?.windows_saved ?? 0)}</p>
          ) : (
            clips.map((clip) => (
              <article key={clip.id} className="proposal">
                <strong>{clip.title || "Untitled"}</strong>
                <div className="meta">
                  <span>{(clip.t_end - clip.t_start).toFixed(1)}s</span>
                  <span className="badge">{clip.status}</span>
                  {broadcastLabel(clip) && <span>{broadcastLabel(clip)}</span>}
                </div>
              </article>
            ))
          )}
        </div>
      )}
    </section>
  );
}

function waitingCopy(saved: number): string {
  if (saved > 0) {
    return `${saved} window${saved === 1 ? "" : "s"} saved. The first one is in the pipeline. Clips show up after transcription.`;
  }
  return "Waiting for the first closed window. That takes about 5 minutes 20 seconds of recording.";
}
