"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { FramingField, type Framing } from "@/components/framing-field";
import { LiveStart } from "@/components/live-start";
import { createFromUrl, deleteProject, listProjects, stageLabel, uploadProject, type Project } from "@/lib/api";

type Segment = "upload" | "link" | "live";

export function HomeClient() {
  const params = useSearchParams();
  const requested = params.get("view");
  const [projects, setProjects] = useState<Project[]>([]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [disclaimer, setDisclaimer] = useState(false);
  const [autopilot, setAutopilot] = useState(true);
  const [clipCount, setClipCount] = useState(10);
  const [uploadAutopilot, setUploadAutopilot] = useState(false);
  const [framing, setFraming] = useState<Framing>("face");
  const [segment, setSegment] = useState<Segment>("upload");

  const refresh = useCallback(async () => {
    try {
      setProjects(await listProjects());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load projects");
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const showNew = requested === "new" || (ready && requested !== "projects" && projects.length === 0);

  async function onUpload(file: File | null) {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const created = await uploadProject(file, { autopilot: uploadAutopilot, clipCount, framing });
      await refresh();
      window.location.href = `/projects/${created.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function onDelete(id: string) {
    if (!window.confirm("Delete this project and its files?")) return;
    setError(null);
    try {
      await deleteProject(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function onUrl(event: FormEvent) {
    event.preventDefault();
    if (!disclaimer) {
      setError("Accept the rights disclaimer before submitting a URL.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const created = await createFromUrl({
        url: url.trim(),
        disclaimer_accepted: true,
        title: title.trim() || undefined,
        autopilot,
        clip_count: autopilot ? clipCount : undefined,
        framing,
      });
      window.location.href = `/projects/${created.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "URL ingest failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className={showNew ? "narrow" : undefined}>
      {showNew ? (
        <>
          <h1>New cut</h1>
          <p className="subtitle">You are responsible for rights to this media. Prefer uploads of content you own.</p>
          <section className="card composer">
            <div className="segments" role="tablist" aria-label="Source">
              {(
                [
                  ["upload", "Upload"],
                  ["link", "Link"],
                  ["live", "Live"],
                ] as const
              ).map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  role="tab"
                  aria-selected={segment === id}
                  className={segment === id ? "segment on" : "segment"}
                  onClick={() => setSegment(id)}
                >
                  {label}
                </button>
              ))}
            </div>
            <FramingField value={framing} disabled={uploading} onChange={setFraming} />

            {segment === "upload" && (
              <div className="stack">
                <label className="field">
                  Clips
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={clipCount}
                    onChange={(e) => setClipCount(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
                    disabled={uploading}
                  />
                </label>
                <input
                  type="file"
                  accept=".mp4,.mov,.webm,.mkv,.m4v,video/*"
                  disabled={uploading}
                  onChange={(e) => void onUpload(e.target.files?.[0] ?? null)}
                />
                <label className="check">
                  <input
                    type="checkbox"
                    checked={uploadAutopilot}
                    onChange={(e) => setUploadAutopilot(e.target.checked)}
                    disabled={uploading}
                  />
                  <span>Autopilot for this upload ({clipCount} clips)</span>
                </label>
              </div>
            )}

            {segment === "link" && (
              <form className="stack" onSubmit={(e) => void onUrl(e)}>
                <input
                  type="url"
                  required
                  placeholder="https://"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={uploading}
                />
                <input
                  type="text"
                  placeholder="Title (optional)"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={uploading}
                />
                <label className="field">
                  Clips
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={clipCount}
                    onChange={(e) => setClipCount(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
                    disabled={uploading}
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={autopilot}
                    onChange={(e) => setAutopilot(e.target.checked)}
                    disabled={uploading}
                  />
                  <span>Autopilot: rank, write post text, and render every clip without further clicks</span>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={disclaimer}
                    onChange={(e) => setDisclaimer(e.target.checked)}
                    disabled={uploading}
                  />
                  <span>
                    I have rights to this media. I understand downloading from a host such as YouTube can violate that
                    site’s terms, and I accept responsibility for copyright.
                  </span>
                </label>
                <button type="submit" disabled={uploading || !disclaimer}>
                  {uploading ? "Working…" : autopilot ? `Make ${clipCount} clips` : "Ingest URL"}
                </button>
                <details className="note">
                  <summary>Rights note</summary>
                  <p>
                    Upload is the path for video you own. URL ingest is optional, off unless{" "}
                    <span className="mono">ALLOW_URL_INGEST=true</span>, and it never tries to bypass DRM, logins, or
                    geo blocks. If a URL fails, upload a file instead.
                  </p>
                </details>
              </form>
            )}

            {segment === "live" && (
              <div className="stack">
                <label className="field">
                  Clips
                  <input
                    type="number"
                    min={1}
                    max={30}
                    value={clipCount}
                    onChange={(e) => setClipCount(Math.max(1, Math.min(30, Number(e.target.value) || 1)))}
                    disabled={uploading}
                  />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={autopilot}
                    onChange={(e) => setAutopilot(e.target.checked)}
                    disabled={uploading}
                  />
                  <span>Autopilot for this capture ({clipCount} clips)</span>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={disclaimer}
                    onChange={(e) => setDisclaimer(e.target.checked)}
                    disabled={uploading}
                  />
                  <span>
                    I have rights to this media. I understand downloading from a host such as YouTube can violate that
                    site’s terms, and I accept responsibility for copyright.
                  </span>
                </label>
                <LiveStart
                  disclaimer={disclaimer}
                  autopilot={autopilot}
                  clipCount={clipCount}
                  framing={framing}
                  uploading={uploading}
                  onBusy={setUploading}
                  onError={setError}
                />
              </div>
            )}
            {uploading && segment === "upload" && <p className="muted">Working…</p>}
          </section>
          {error && <p className="bad">{error}</p>}
        </>
      ) : (
        <>
          <div className="bench-head">
            <h1>Projects</h1>
            <Link href="/?view=new">New cut</Link>
          </div>
          {(loadError || error) && <p className="bad">{loadError || error}</p>}
          {!ready ? (
            <p className="muted">Loading projects…</p>
          ) : projects.length === 0 ? (
            <p className="muted">
              No projects yet. <Link href="/?view=new">New cut</Link>
            </p>
          ) : (
            <ul className="project-list">
              {projects.map((project) => {
                const failed = project.latest_job?.status === "failed" || Boolean(project.latest_job?.error);
                const running =
                  project.status === "processing" ||
                  project.status === "ingesting" ||
                  project.status === "created" ||
                  project.status === "exporting" ||
                  project.status === "live";
                const readyStatus = project.status === "ready_for_review" || project.status === "done";
                const pip = failed ? "cut" : running ? "hold" : readyStatus ? "take" : "idle";
                return (
                  <li key={project.id} className="project-row">
                    <span className={`pip ${pip}`} aria-hidden="true" />
                    <Link href={`/projects/${project.id}`} className="project-main">
                      <strong>{project.title || project.id.slice(0, 8)}</strong>
                      <span className="project-meta">
                        <span className="badge">{project.status}</span>
                        <span className="badge">{project.source_type}</span>
                        {project.latest_job && (
                          <span>
                            {stageLabel(project.latest_job.stage)} · {Math.round((project.latest_job.progress || 0) * 100)}%
                          </span>
                        )}
                      </span>
                    </Link>
                    {project.latest_job?.error && <span className="bad project-error">{project.latest_job.error}</span>}
                    <button className="text-danger" type="button" onClick={() => void onDelete(project.id)}>
                      Delete
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </main>
  );
}
