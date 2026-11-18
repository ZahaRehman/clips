"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AutopilotGallery } from "@/components/autopilot-gallery";
import { ChunkBoard } from "@/components/chunk-board";
import { FramingField, framingOf, type Framing } from "@/components/framing-field";
import { LiveCapture } from "@/components/live-capture";
import { ReviewSections } from "@/components/review-sections";
import {
  createClip,
  deleteClip,
  deleteProject,
  enqueueExport,
  getProject,
  getTranscript,
  listClips,
  listPublishes,
  listTikTokPublishes,
  proposeClips,
  publishClips,
  publishTikTok,
  redraftPostText,
  stageLabel,
  stopLive,
  updateClip,
  updateFraming,
  tiktokStatus,
  youtubeStatus,
  type Clip,
  type ClipExport,
  type CropMode,
  type JobOut,
  type Project,
  type PublishRequest,
  type PublishRow,
  type TikTokPublishRequest,
  type TikTokPublishRow,
  type TikTokStatus,
  type Transcript,
  type YouTubeStatus,
} from "@/lib/api";

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const videoRef = useRef<HTMLVideoElement>(null);
  const previewRef = useRef<HTMLVideoElement>(null);

  const [project, setProject] = useState<Project | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [exportsByClip, setExportsByClip] = useState<Record<string, ClipExport[]>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editStart, setEditStart] = useState(0);
  const [editEnd, setEditEnd] = useState(20);
  const [editTitle, setEditTitle] = useState("");
  const [manualStart, setManualStart] = useState(0);
  const [manualEnd, setManualEnd] = useState(15);
  const [manualTitle, setManualTitle] = useState("");
  const [cropMode, setCropMode] = useState<CropMode>("face");
  const [captionText, setCaptionText] = useState("");
  const [customX, setCustomX] = useState(0.2);
  const [customY, setCustomY] = useState(0);
  const [customW, setCustomW] = useState(0.56);
  const [customH, setCustomH] = useState(1);
  const [pickMode, setPickMode] = useState<"start" | "end">("start");
  const [youtube, setYoutube] = useState<YouTubeStatus | null>(null);
  const [publishes, setPublishes] = useState<PublishRow[]>([]);
  const [tiktok, setTiktok] = useState<TikTokStatus | null>(null);
  const [tiktokPublishes, setTiktokPublishes] = useState<TikTokPublishRow[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState(false);

  const chunked = Boolean(project?.chunks) || project?.source_meta?.chunked === true;
  const processing = useMemo(() => {
    const s = project?.status;
    return s === "ingesting" || s === "processing" || s === "created";
  }, [project?.status]);

  const refresh = useCallback(async () => {
    try {
      const p = await getProject(projectId);
      setProject(p);
      setOffline(false);
      if (
        p.status === "live" ||
        p.status === "ready_for_review" ||
        p.status === "done" ||
        p.status === "exporting" ||
        p.source_meta?.chunked === true
      ) {
        try {
          setTranscript(await getTranscript(projectId));
        } catch {
          /* transcript may lag one poll */
        }
        const cs = await listClips(projectId);
        setClips(cs);
        setExportsByClip(Object.fromEntries(cs.map((c) => [c.id, c.latest_export ? [c.latest_export] : []])));
        try {
          setPublishes(await listPublishes(projectId));
        } catch {
          /* publish status lags one poll */
        }
        try {
          setTiktokPublishes(await listTikTokPublishes(projectId));
        } catch {
          /* tiktok status lags one poll */
        }
      }
      try {
        setYoutube(await youtubeStatus());
      } catch {
        setYoutube(null);
      }
      try {
        setTiktok(await tiktokStatus());
      } catch {
        setTiktok(null);
      }
    } catch {
      setOffline(true);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
    const id = setInterval(() => void refresh(), 2500);
    return () => clearInterval(id);
  }, [refresh]);

  const selected = clips.find((c) => c.id === selectedId) ?? null;
  const proposals = clips.filter((c) => c.status === "proposed" || c.score > 0 || c.reason);
  const manual = clips.filter((c) => !proposals.includes(c));
  const autopilotClips = clips.filter((c) => c.latest_export || c.post_meta_source || c.status === "rejected");
  const rangeStart = selected ? editStart : manualStart;
  const rangeEnd = selected ? editEnd : manualEnd;
  const job = project?.latest_job;
  const progress = Math.max(0, Math.min(1, job?.progress || 0));
  const showReview = Boolean(transcript) && (!processing || chunked);
  const jobError = !offline && job?.status === "failed" ? job.error : null;
  const banner = offline ? "Cannot reach the API. This page will keep retrying." : notice || jobError;

  function seek(seconds: number) {
    const node = videoRef.current;
    if (!node) return;
    node.currentTime = Math.max(0, seconds);
  }

  function selectClip(clip: Clip) {
    setSelectedId(clip.id);
    setEditStart(clip.t_start);
    setEditEnd(clip.t_end);
    setEditTitle(clip.title);
    setCropMode(clip.crop?.mode || "face");
    setCustomX(clip.crop?.x ?? 0.2);
    setCustomY(clip.crop?.y ?? 0);
    setCustomW(clip.crop?.w ?? 0.56);
    setCustomH(clip.crop?.h ?? 1);
    setCaptionText(captionDraft(clip, transcript));
    setPickMode("start");
    seek(clip.t_start);
  }

  function onWordClick(start: number, end: number) {
    if (selected) {
      if (pickMode === "start") {
        setEditStart(start);
        setPickMode("end");
      } else {
        setEditEnd(Math.max(end, editStart + 0.2));
        setPickMode("start");
      }
      return;
    }
    if (pickMode === "start") {
      setManualStart(start);
      setPickMode("end");
    } else {
      setManualEnd(Math.max(end, manualStart + 0.5));
      setPickMode("start");
    }
  }

  async function saveSelected(patch: Parameters<typeof updateClip>[1]) {
    if (!selected) return;
    setBusy(true);
    setNotice(null);
    try {
      const updated = await updateClip(selected.id, patch);
      setEditStart(updated.t_start);
      setEditEnd(updated.t_end);
      setEditTitle(updated.title);
      if (patch.crop) setCropMode(updated.crop?.mode || patch.crop.mode);
      if (patch.caption_text_override !== undefined) setCaptionText(captionDraft(updated, transcript));
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateClip() {
    setBusy(true);
    setNotice(null);
    try {
      await createClip(projectId, {
        t_start: Number(manualStart),
        t_end: Number(manualEnd),
        title: manualTitle || undefined,
      });
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Create clip failed");
    } finally {
      setBusy(false);
    }
  }

  async function onExport(clipId: string, force = false) {
    setBusy(true);
    setNotice(null);
    try {
      await enqueueExport(clipId, force);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteClip(clipId: string) {
    if (!window.confirm("Delete this clip and its export files?")) return;
    setBusy(true);
    setNotice(null);
    try {
      await deleteClip(clipId);
      if (selectedId === clipId) setSelectedId(null);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteProject() {
    if (!window.confirm("Delete this project and its files?")) return;
    setBusy(true);
    setNotice(null);
    try {
      await deleteProject(projectId);
      window.location.href = "/";
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Delete failed");
      setBusy(false);
    }
  }

  async function onPatchClip(clipId: string, patch: Parameters<typeof updateClip>[1]) {
    setBusy(true);
    setNotice(null);
    try {
      await updateClip(clipId, patch);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Update failed");
    } finally {
      setBusy(false);
    }
  }

  async function onTikTokPublish(body: TikTokPublishRequest) {
    setBusy(true);
    setNotice(null);
    try {
      await publishTikTok(projectId, body);
      setHint("Posted to TikTok. It can take a few minutes before the posts show in the TikTok app.");
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function onPublish(body: PublishRequest) {
    setBusy(true);
    setNotice(null);
    try {
      const result = await publishClips(projectId, body);
      setHint(result.warnings.length ? result.warnings.join(" ") : null);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function onRedraft() {
    setBusy(true);
    setNotice(null);
    try {
      await redraftPostText(projectId);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Redraft failed");
    } finally {
      setBusy(false);
    }
  }

  function openInEditor(clip: Clip) {
    selectClip(clip);
    setEditorOpen(true);
  }

  async function onStopLive() {
    setBusy(true);
    setNotice(null);
    try {
      await stopLive(projectId);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Stop failed");
    } finally {
      setBusy(false);
    }
  }

  async function onFraming(value: Framing) {
    if (!project || value === (project.framing || "face")) return;
    setBusy(true);
    setNotice(null);
    try {
      await updateFraming(projectId, value);
      setHint(
        value === "full"
          ? "New clips keep the whole screen. Shorts that already finished are being rendered again."
          : value === "gameplay"
            ? "New clips keep the gameplay. Shorts that already finished are being framed again."
            : "New clips follow the face. Shorts that already finished are being framed again.",
      );
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not change the frame");
    } finally {
      setBusy(false);
    }
  }

  async function onRerank() {
    setBusy(true);
    setNotice(null);
    try {
      await proposeClips(projectId);
      await refresh();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Re-rank failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="wide">
      <header className="bench-head">
        <div>
          <h1>{project?.title || "Project"}</h1>
          <p className="subtitle">
            <span className="badge">{project?.status || "…"}</span>
            {project?.source_type && <span className="badge">{project.source_type}</span>}
            {job && (
              <>
                {" "}
                · {jobHeadline(job, project)} · {Math.round(progress * 100)}%
              </>
            )}
          </p>
        </div>
        <button className="text-danger" disabled={busy || !project} onClick={() => void onDeleteProject()}>
          Delete project
        </button>
      </header>
      {job && (
        <div className="progress" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ transform: `scaleX(${progress})` }} />
        </div>
      )}
      {project && (
        <section className="card" style={{ marginBottom: "1rem" }}>
          <FramingField
            value={framingOf(project.framing)}
            disabled={busy}
            onChange={(value) => void onFraming(value)}
          />
        </section>
      )}
      {banner && <p className="bad">{banner}</p>}
      {hint && <p className="banner">{hint}</p>}
      {job?.kind === "export" && job.status === "queued" && !offline && (
        <p className="muted">Export is queued and waiting for the worker.</p>
      )}
      {processing && !chunked && <p className="muted">Pipeline is running. This page refreshes on its own.</p>}
      {project?.chunks && <ChunkBoard chunks={project.chunks} />}

      {project && (
        <LiveCapture project={project} clips={clips} busy={busy} onStop={() => void onStopLive()} />
      )}

      {project?.autopilot && editorOpen && (
        <button className="secondary" type="button" onClick={() => setEditorOpen(false)}>
          Back to shorts
        </button>
      )}

      {project?.autopilot && (!processing || chunked) && !editorOpen && (
        <AutopilotGallery
          summary={project.autopilot_summary}
          clips={autopilotClips}
          busy={busy}
          youtube={youtube}
          publishes={publishes}
          onPatch={onPatchClip}
          onOpen={openInEditor}
          onRedraft={() => void onRedraft()}
          onPublish={onPublish}
          tiktok={tiktok}
          tiktokPublishes={tiktokPublishes}
          onTikTokPublish={onTikTokPublish}
        />
      )}

      <div id="editor" />
      {showReview && transcript && (!project?.autopilot || editorOpen) && (
        <ReviewSections
          transcript={transcript}
          proposals={proposals}
          manual={manual}
          selected={selected}
          selectedId={selectedId}
          busy={busy}
          pickMode={pickMode}
          rangeStart={rangeStart}
          rangeEnd={rangeEnd}
          editTitle={editTitle}
          editStart={editStart}
          editEnd={editEnd}
          manualTitle={manualTitle}
          manualStart={manualStart}
          manualEnd={manualEnd}
          exportsByClip={exportsByClip}
          cropMode={cropMode}
          captionText={captionText}
          customX={customX}
          customY={customY}
          customW={customW}
          customH={customH}
          videoRef={videoRef}
          previewRef={previewRef}
          onRerank={() => void onRerank()}
          onSelect={selectClip}
          onSave={() => void saveSelected({ t_start: editStart, t_end: editEnd, title: editTitle })}
          onAccept={() =>
            void saveSelected({ status: "accepted", title: editTitle, t_start: editStart, t_end: editEnd })
          }
          onReject={() => void saveSelected({ status: "rejected" })}
          onExport={(id) => void onExport(id)}
          onReexport={(id) => void onExport(id, true)}
          onDelete={(id) => void onDeleteClip(id)}
          onSaveCrop={() =>
            void saveSelected({
              crop:
                cropMode === "custom"
                  ? { mode: cropMode, x: customX, y: customY, w: customW, h: customH }
                  : { mode: cropMode },
            })
          }
          onSaveCaption={() => void saveSelected({ caption_text_override: captionText.trim() })}
          onWord={onWordClick}
          onCreate={() => void onCreateClip()}
          setCropMode={setCropMode}
          setCaptionText={setCaptionText}
          setCustomX={setCustomX}
          setCustomY={setCustomY}
          setCustomW={setCustomW}
          setCustomH={setCustomH}
          setEditTitle={setEditTitle}
          setEditStart={setEditStart}
          setEditEnd={setEditEnd}
          setManualTitle={setManualTitle}
          setManualStart={setManualStart}
          setManualEnd={setManualEnd}
        />
      )}
    </main>
  );
}

function captionDraft(clip: Clip, transcript: Transcript | null): string {
  const raw = clip.caption_text_override?.trim();
  if (raw?.startsWith("{")) {
    try {
      const data = JSON.parse(raw) as { words?: { word?: string }[] };
      if (Array.isArray(data.words)) {
        return data.words.map((word) => word.word || "").filter(Boolean).join(" ");
      }
    } catch {
      /* plain text below */
    }
  }
  if (raw) return raw;
  if (!transcript) return "";
  return transcript.words
    .filter((word) => {
      const mid = (word.start + word.end) / 2;
      return mid >= clip.t_start && mid <= clip.t_end;
    })
    .map((word) => word.word)
    .join(" ");
}

function jobHeadline(job: JobOut, project: Project | null): string {
  if (job.kind === "export") {
    if (job.status === "queued") return "Export queued";
    if (job.status === "failed") return "Export failed";
    if (job.status === "succeeded") return "Export ready";
    return "Exporting";
  }
  if (job.kind === "metadata") {
    if (job.status === "queued") return "Post text queued";
    if (job.status === "failed") return "Post text failed";
    if (job.status === "succeeded") return "Post text ready";
    return "Writing post text";
  }
  const render = project?.autopilot_summary;
  if (job.stage === "render_clips" && job.status === "running" && render?.render_total) {
    return `Rendering clip ${render.render_current} of ${render.render_total}`;
  }
  return stageLabel(job.stage || job.status);
}
