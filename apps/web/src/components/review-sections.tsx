import type { RefObject } from "react";
import { CropCaptionControls, cropObjectPosition } from "@/components/clip-controls";
import { broadcastLabel, fileUrl, type Clip, type ClipExport, type CropMode, type Transcript } from "@/lib/api";

type Props = {
  transcript: Transcript;
  proposals: Clip[];
  manual: Clip[];
  selected: Clip | null;
  selectedId: string | null;
  busy: boolean;
  pickMode: "start" | "end";
  rangeStart: number;
  rangeEnd: number;
  editTitle: string;
  editStart: number;
  editEnd: number;
  manualTitle: string;
  manualStart: number;
  manualEnd: number;
  cropMode: CropMode;
  captionText: string;
  customX: number;
  customY: number;
  customW: number;
  customH: number;
  exportsByClip: Record<string, ClipExport[]>;
  onRerank: () => void;
  onSelect: (clip: Clip) => void;
  onSave: () => void;
  onAccept: () => void;
  onReject: () => void;
  onExport: (id: string) => void;
  onReexport: (id: string) => void;
  onDelete: (id: string) => void;
  onSaveCrop: () => void;
  onSaveCaption: () => void;
  onWord: (start: number, end: number) => void;
  onCreate: () => void;
  setCropMode: (value: CropMode) => void;
  setCaptionText: (value: string) => void;
  setCustomX: (value: number) => void;
  setCustomY: (value: number) => void;
  setCustomW: (value: number) => void;
  setCustomH: (value: number) => void;
  setEditTitle: (value: string) => void;
  setEditStart: (value: number) => void;
  setEditEnd: (value: number) => void;
  setManualTitle: (value: string) => void;
  setManualStart: (value: number) => void;
  setManualEnd: (value: number) => void;
  videoRef: RefObject<HTMLVideoElement | null>;
  previewRef: RefObject<HTMLVideoElement | null>;
};

export function ReviewSections(props: Props) {
  const { transcript, selected } = props;
  const mark = `In ${props.rangeStart.toFixed(2)}s · Out ${props.rangeEnd.toFixed(2)}s · ${(props.rangeEnd - props.rangeStart).toFixed(1)}s`;

  return (
    <>
      <div className="edit-bench">
        <div className="gate-col">
          <p className="mono cut-marks">{mark}</p>
          <div className={props.cropMode === "full" ? "phone landscape" : "phone"}>
            <video
              ref={props.previewRef}
              muted
              playsInline
              src={fileUrl(transcript.proxy_url)}
              style={{ objectPosition: cropObjectPosition(props) }}
            />
          </div>
          <video
            ref={props.videoRef}
            className="wide-video"
            controls
            src={fileUrl(transcript.proxy_url)}
            onTimeUpdate={(event) => syncPreview(event.currentTarget, props.previewRef.current)}
            onPlay={() => props.previewRef.current?.play().catch(() => undefined)}
            onPause={() => props.previewRef.current?.pause()}
          />
          {selected && (
            <CropCaptionControls
              selectedId={props.selectedId}
              busy={props.busy}
              cropMode={props.cropMode}
              captionText={props.captionText}
              customX={props.customX}
              customY={props.customY}
              customW={props.customW}
              customH={props.customH}
              exportsByClip={props.exportsByClip}
              onSaveCrop={props.onSaveCrop}
              onSaveCaption={props.onSaveCaption}
              setCropMode={props.setCropMode}
              setCaptionText={props.setCaptionText}
              setCustomX={props.setCustomX}
              setCustomY={props.setCustomY}
              setCustomW={props.setCustomW}
              setCustomH={props.setCustomH}
            />
          )}
        </div>

        <section className="card script-col">
          <h2>Transcript</h2>
          <p className="muted">
            Click words to set {props.pickMode === "start" ? "in" : "out"}
            {selected ? " on the selected clip." : " for a manual clip."}
          </p>
          <div className="transcript">
            {transcript.words.map((w, i) => {
              const active = w.start >= props.rangeStart && w.end <= props.rangeEnd;
              return (
                <button
                  key={`${w.start}-${i}`}
                  type="button"
                  className={active ? "word active" : "word"}
                  onClick={() => props.onWord(w.start, w.end)}
                >
                  {w.word}
                </button>
              );
            })}
          </div>
          {selected && (
            <div className="script-actions">
              <h2>Selected</h2>
              <div className="row-inputs">
                <label>
                  Title
                  <input value={props.editTitle} onChange={(e) => props.setEditTitle(e.target.value)} />
                </label>
                <label>
                  In
                  <input type="number" step="0.1" value={props.editStart} onChange={(e) => props.setEditStart(Number(e.target.value))} />
                </label>
                <label>
                  Out
                  <input type="number" step="0.1" value={props.editEnd} onChange={(e) => props.setEditEnd(Number(e.target.value))} />
                </label>
              </div>
              <div className="actions">
                <button disabled={props.busy} onClick={props.onSave}>
                  Save trim
                </button>
                <button className="secondary" disabled={props.busy} onClick={props.onAccept}>
                  Accept
                </button>
                <button className="secondary" disabled={props.busy} onClick={props.onReject}>
                  Reject
                </button>
                <button
                  disabled={props.busy || !canExport(selected.status)}
                  onClick={() => props.onExport(selected.id)}
                >
                  {exportButtonLabel(selected.status)}
                </button>
                <button
                  className="secondary"
                  disabled={props.busy || !canExport(selected.status)}
                  onClick={() => props.onReexport(selected.id)}
                >
                  Re-export
                </button>
                <button className="text-danger" disabled={props.busy} onClick={() => props.onDelete(selected.id)}>
                  Delete clip
                </button>
                <ExportLink clipId={selected.id} exportsByClip={props.exportsByClip} />
              </div>
            </div>
          )}
        </section>
      </div>

      <section className="card filmstrip">
        <div className="bench-head">
          <h2>Proposals</h2>
          <button className="secondary" disabled={props.busy} onClick={props.onRerank}>
            Re-rank from transcript
          </button>
        </div>
        {props.proposals.length === 0 ? (
          <p className="muted">No proposals yet. Use manual trim below, or re-rank after a transcript exists.</p>
        ) : (
          <div className="gallery">
            {props.proposals.map((clip) => (
              <button
                key={clip.id}
                type="button"
                className={`proposal${clip.id === props.selectedId ? " selected" : ""}${clip.status === "rejected" ? " rejected" : ""}`}
                onClick={() => props.onSelect(clip)}
              >
                <strong>{clip.title || "Untitled"}</strong>
                <div className="meta">
                  <span>#{clip.rank || "–"}</span>
                  <span>score {clip.score.toFixed(0)}</span>
                  <span>{(clip.t_end - clip.t_start).toFixed(1)}s</span>
                  {broadcastLabel(clip) && <span>{broadcastLabel(clip)}</span>}
                  <span className="badge">{clip.status}</span>
                  <ExportMark clipId={clip.id} exportsByClip={props.exportsByClip} />
                </div>
                {clip.reason && <p className="reason">{clip.reason}</p>}
              </button>
            ))}
          </div>
        )}
      </section>

      <section className="card" style={{ marginBottom: "1rem" }}>
        <h2>Manual clip</h2>
        <p className="muted">Creates an accepted clip without using a proposal.</p>
        <div className="row-inputs">
          <label>
            Start
            <input type="number" step="0.1" value={props.manualStart} onChange={(e) => props.setManualStart(Number(e.target.value))} />
          </label>
          <label>
            End
            <input type="number" step="0.1" value={props.manualEnd} onChange={(e) => props.setManualEnd(Number(e.target.value))} />
          </label>
          <label>
            Title
            <input value={props.manualTitle} onChange={(e) => props.setManualTitle(e.target.value)} placeholder="Optional" />
          </label>
          <button disabled={props.busy} onClick={props.onCreate}>
            Create clip
          </button>
        </div>
        {props.manual.length > 0 && (
          <ul className="list">
            {props.manual.map((c) => (
              <ClipRow
                key={c.id}
                clip={c}
                busy={props.busy}
                exportsByClip={props.exportsByClip}
                onExport={props.onExport}
                onReexport={props.onReexport}
                onDelete={props.onDelete}
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function syncPreview(main: HTMLVideoElement, preview: HTMLVideoElement | null) {
  if (!preview) return;
  if (Math.abs(preview.currentTime - main.currentTime) > 0.3) {
    preview.currentTime = main.currentTime;
  }
}

function canExport(status: string): boolean {
  return status === "accepted" || status === "exported";
}

function exportButtonLabel(status: string): string {
  return canExport(status) ? "Export" : "Accept to export";
}

function ExportMark({
  clipId,
  exportsByClip,
}: {
  clipId: string;
  exportsByClip: Record<string, ClipExport[]>;
}) {
  const ex = exportsByClip[clipId]?.[0];
  if (!ex) return null;
  return <span className="badge">export {ex.status}</span>;
}

function ExportLink({
  clipId,
  exportsByClip,
}: {
  clipId: string;
  exportsByClip: Record<string, ClipExport[]>;
}) {
  const ex = exportsByClip[clipId]?.[0];
  if (!ex) return null;
  return (
    <>
      <span className="badge">{ex.status}</span>
      {ex.status === "ready" && ex.download_url && (
        <a href={fileUrl(ex.download_url)} download>
          Download
        </a>
      )}
    </>
  );
}

function ClipRow({
  clip,
  busy,
  exportsByClip,
  onExport,
  onReexport,
  onDelete,
}: {
  clip: Clip;
  busy: boolean;
  exportsByClip: Record<string, ClipExport[]>;
  onExport: (id: string) => void;
  onReexport: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  return (
    <li>
      <strong>{clip.title}</strong>{" "}
      <span className="mono">
        {clip.t_start.toFixed(1)}s → {clip.t_end.toFixed(1)}s
        {broadcastLabel(clip) ? ` · ${broadcastLabel(clip)}` : ""}
      </span>
      <span className="badge">{clip.status}</span>
      <div className="actions">
        <button disabled={busy || !canExport(clip.status)} onClick={() => onExport(clip.id)}>
          {exportButtonLabel(clip.status)}
        </button>
        <button className="secondary" disabled={busy || !canExport(clip.status)} onClick={() => onReexport(clip.id)}>
          Re-export
        </button>
        <button className="secondary" disabled={busy} onClick={() => onDelete(clip.id)}>
          Delete
        </button>
        <ExportLink clipId={clip.id} exportsByClip={exportsByClip} />
      </div>
    </li>
  );
}
