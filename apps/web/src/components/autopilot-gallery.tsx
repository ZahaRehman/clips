import { useEffect, useState } from "react";
import { TikTokBar } from "@/components/tiktok-publish";
import { PublishBar, type PublishItem } from "@/components/youtube-publish";
import {
  broadcastLabel,
  fileUrl,
  type AutopilotSummary,
  type Clip,
  type PublishRequest,
  type PublishRow,
  type TikTokPublishRequest,
  type TikTokPublishRow,
  type TikTokStatus,
  type YouTubeStatus,
  type updateClip,
} from "@/lib/api";

type PostPatch = Pick<Parameters<typeof updateClip>[1], "post_title" | "post_description" | "post_hashtags" | "status">;

type Props = {
  summary: AutopilotSummary | null | undefined;
  clips: Clip[];
  busy: boolean;
  youtube: YouTubeStatus | null;
  publishes: PublishRow[];
  tiktok: TikTokStatus | null;
  tiktokPublishes: TikTokPublishRow[];
  onPatch: (clipId: string, patch: PostPatch) => Promise<void>;
  onOpen: (clip: Clip) => void;
  onRedraft: () => void;
  onPublish: (body: PublishRequest) => Promise<void>;
  onTikTokPublish: (body: TikTokPublishRequest) => Promise<void>;
};

const REASONS: Record<string, string> = {
  GEMINI_NO_KEY: "GEMINI_API_KEY is not set",
  GEMINI_QUOTA_DAY: "Gemini daily quota reached; resets at midnight Pacific",
  GEMINI_QUOTA_MINUTE: "Gemini per-minute quota reached",
  GEMINI_TIMEOUT: "Gemini timed out",
  GEMINI_BAD_RESPONSE: "Gemini returned an unusable answer",
  GEMINI_HTTP: "Gemini request failed",
  GEMINI_INCOMPLETE: "Gemini skipped some clips",
  LLM_TOO_FEW_CLIPS: "the model returned too few usable clips",
  LLM_NO_VALID_CLIPS: "the model returned no usable clips",
  LLM_HTTP: "the model server could not be reached",
  LLM_BAD_RESPONSE: "the model returned an unusable answer",
  LLM_UNKNOWN_PROVIDER: "LLM_PROVIDER is not recognized",
};

const STATUS_LABEL: Record<string, string> = {
  queued: "Queued",
  uploading: "Uploading",
  processing: "Processing",
  scheduled: "Scheduled",
  live: "On YouTube",
  failed: "Failed",
};

function describeError(code: string | null | undefined): string {
  if (!code) return "";
  return REASONS[code] || code;
}

function durationOf(clip: Clip): number {
  return clip.latest_export?.duration_sec ?? clip.t_end - clip.t_start;
}

function canPost(clip: Clip): boolean {
  const exp = clip.latest_export;
  const ready = Boolean(exp?.status === "ready" && exp.download_url);
  return ready && clip.status !== "rejected" && durationOf(clip) <= 180 && Boolean((clip.post_title || "").trim());
}

function postHint(clip: Clip): string {
  if (clip.status === "rejected") return "Excluded";
  if (clip.latest_export?.status !== "ready") return "Needs a finished export";
  if (durationOf(clip) > 180) return "Longer than 180s";
  if (!(clip.post_title || "").trim()) return "Save a title first";
  return "Include in post";
}

export function AutopilotGallery({
  summary,
  clips,
  busy,
  youtube,
  publishes,
  tiktok,
  tiktokPublishes,
  onPatch,
  onOpen,
  onRedraft,
  onPublish,
  onTikTokPublish,
}: Props) {
  const publishOn = Boolean(youtube?.configured);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState<Record<string, boolean>>({});
  const latest = latestByClip(publishes);
  const latestTikTok = latestTikTokByClip(tiktokPublishes);

  useEffect(() => {
    setPicked((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const clip of clips) {
        if (next[clip.id] !== undefined) continue;
        next[clip.id] = canPost(clip);
        changed = true;
      }
      return changed ? next : prev;
    });
  }, [clips]);

  const items: PublishItem[] = clips.map((clip) => ({
    id: clip.id,
    title: clip.post_title || "",
    description: clip.post_description || "",
    duration: durationOf(clip),
    picked: Boolean(picked[clip.id]),
    selectable: canPost(clip),
    dirty: Boolean(dirty[clip.id]),
  }));

  return (
    <section className="card" style={{ marginBottom: "1rem" }}>
      <h2>Shorts</h2>
      <AutopilotBanners summary={summary} />
      <div className="actions" style={{ marginBottom: "0.75rem" }}>
        <button className="secondary" disabled={busy} onClick={onRedraft}>
          Redraft post text
        </button>
        <span className="muted">Edited drafts are kept.</span>
      </div>
      {clips.length === 0 ? (
        <p className="muted">No clips yet.</p>
      ) : (
        <div className="shorts-grid">
          {clips.map((clip) => (
            <ShortCard
              key={`${clip.id}:${clip.post_meta_source}:${clip.post_title}:${clip.post_description}`}
              clip={clip}
              busy={busy}
              publishOn={publishOn}
              picked={Boolean(picked[clip.id])}
              publish={latest.get(clip.id)}
              tiktokPublish={latestTikTok.get(clip.id)}
              onPick={(value) => setPicked((prev) => ({ ...prev, [clip.id]: value }))}
              onDirty={(value) =>
                setDirty((prev) => (prev[clip.id] === value ? prev : { ...prev, [clip.id]: value }))
              }
              onPatch={onPatch}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}
      {publishOn && youtube && (
        <PublishBar status={youtube} items={items} publishes={publishes} busy={busy} onPost={onPublish} />
      )}
      {tiktok?.configured && (
        <TikTokBar status={tiktok} items={items} publishes={tiktokPublishes} busy={busy} onPost={onTikTokPublish} />
      )}
    </section>
  );
}

function tiktokLabel(row: TikTokPublishRow): string {
  if (row.status === "posted" && !row.tiktok_post_id) return "Posted";
  if (row.status === "posted") return "Posted";
  if (row.status === "failed") return "Failed";
  if (row.status === "processing") return "Processing";
  if (row.status === "uploading") return "Uploading";
  return "Queued";
}

function latestTikTokByClip(rows: TikTokPublishRow[]): Map<string, TikTokPublishRow> {
  const map = new Map<string, TikTokPublishRow>();
  for (const row of rows) {
    if (!map.has(row.clip_id)) map.set(row.clip_id, row);
  }
  return map;
}

function latestByClip(rows: PublishRow[]): Map<string, PublishRow> {
  const map = new Map<string, PublishRow>();
  for (const row of rows) {
    if (!map.has(row.clip_id)) map.set(row.clip_id, row);
  }
  return map;
}

function AutopilotBanners({ summary }: { summary: AutopilotSummary | null | undefined }) {
  if (!summary) return null;
  const rankedBy = summary.provider_used || "heuristic";
  return (
    <div className="stack" style={{ marginBottom: "0.75rem" }}>
      <p className="muted" style={{ margin: 0 }}>
        {summary.rendered} rendered of {summary.planned ?? summary.requested} planned ({summary.requested} requested) ·
        ranked by {rankedBy}
      </p>
      {summary.plan_reason && <p className="banner">{summary.plan_reason}</p>}
      {summary.llm_error && (
        <p className="banner warn">Ranking fell back to heuristics: {describeError(summary.llm_error)}.</p>
      )}
      {summary.metadata_error && (
        <p className="banner warn">
          Post text uses fallback drafts: {summary.metadata_error_message || describeError(summary.metadata_error)}.
        </p>
      )}
      {summary.failed > 0 && (
        <div className="banner bad">
          {summary.failed} clip(s) failed to render.
          <ul>
            {summary.render_errors.map((item) => (
              <li key={item.clip_id}>{item.message}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ShortCard({
  clip,
  busy,
  publishOn,
  picked,
  publish,
  tiktokPublish,
  onPick,
  onDirty,
  onPatch,
  onOpen,
}: {
  clip: Clip;
  busy: boolean;
  publishOn: boolean;
  picked: boolean;
  publish?: PublishRow;
  tiktokPublish?: TikTokPublishRow;
  onPick: (value: boolean) => void;
  onDirty: (value: boolean) => void;
  onPatch: Props["onPatch"];
  onOpen: Props["onOpen"];
}) {
  const [title, setTitle] = useState(clip.post_title || clip.title);
  const [body, setBody] = useState(clip.post_body);
  const [tags, setTags] = useState(clip.post_hashtags.join(" "));
  const exp = clip.latest_export;
  const ready = exp?.status === "ready" && exp.download_url;
  const excluded = clip.status === "rejected";
  const duration = durationOf(clip);
  const selectable = canPost(clip);
  const isDirty =
    title !== (clip.post_title || clip.title) || body !== clip.post_body || tags !== clip.post_hashtags.join(" ");

  useEffect(() => {
    onDirty(isDirty);
  }, [isDirty, onDirty]);

  function save() {
    void onPatch(clip.id, {
      post_title: title,
      post_description: body,
      post_hashtags: tags.split(/[\s,]+/).filter(Boolean),
    });
  }

  return (
    <article className={`short-card${excluded ? " excluded" : ""}`}>
      {ready ? (
        <video
          className="short-video"
          controls
          playsInline
          preload="metadata"
          src={fileUrl(exp.download_url!)}
          style={exp.width && exp.height ? { aspectRatio: `${exp.width} / ${exp.height}` } : undefined}
        />
      ) : (
        <div className="short-video placeholder">{exp ? `Export ${exp.status}` : "Not rendered"}</div>
      )}
      {exp?.status === "failed" && exp.error && <p className="bad small">{exp.error}</p>}
      <div className="meta">
        <span>#{clip.rank || "–"}</span>
        <span>{duration.toFixed(1)}s</span>
        {broadcastLabel(clip) && <span>{broadcastLabel(clip)}</span>}
        <span>score {clip.score.toFixed(0)}</span>
        {clip.post_meta_source && <span className="badge">{clip.post_meta_source}</span>}
        {clip.post_meta_stale && <span className="badge warn">stale</span>}
        {publishOn && duration > 60 && duration <= 180 && <span className="badge warn">Over 60s</span>}
        {publishOn && duration > 180 && <span className="badge warn">Over 180s</span>}
        {publish && <span className={`badge${publish.status === "failed" ? " warn" : ""}`}>{STATUS_LABEL[publish.status] || publish.status}</span>}
        {tiktokPublish && (
          <span className={`badge${tiktokPublish.status === "failed" ? " warn" : ""}`}>
            TikTok {tiktokLabel(tiktokPublish)}
          </span>
        )}
      </div>
      {tiktokPublish?.tiktok_post_id && (
        <a href={`https://www.tiktok.com/video/${tiktokPublish.tiktok_post_id}`} target="_blank" rel="noreferrer">
          Open on TikTok
        </a>
      )}
      {tiktokPublish?.status === "posted" && !tiktokPublish.tiktok_post_id && (
        <p className="muted small">Posted to TikTok</p>
      )}
      {tiktokPublish?.status === "failed" && tiktokPublish.error_message && (
        <p className="bad small">{tiktokPublish.error_message}</p>
      )}
      {publish?.watch_url && (
        <a href={publish.watch_url} target="_blank" rel="noreferrer">
          Open on YouTube
        </a>
      )}
      {publish?.status === "failed" && publish.error_message && <p className="bad small">{publish.error_message}</p>}
      {publishOn && (
        <label className="check">
          <input
            type="checkbox"
            checked={picked && selectable}
            disabled={!selectable || busy}
            onChange={(e) => onPick(e.target.checked)}
          />
          <span>{postHint(clip)}</span>
        </label>
      )}
      <label className="field">
        Title <span className="muted">{title.length}/100</span>
        <input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label className="field">
        Description
        <textarea value={body} onChange={(e) => setBody(e.target.value)} />
      </label>
      <label className="field">
        Hashtags
        <input value={tags} placeholder="#tag #tag #tag" onChange={(e) => setTags(e.target.value)} />
      </label>
      <div className="actions">
        <button disabled={busy || !isDirty} onClick={save}>
          Save text
        </button>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => void onPatch(clip.id, { status: excluded ? "accepted" : "rejected" })}
        >
          {excluded ? "Include" : "Exclude"}
        </button>
        {ready && (
          <a href={fileUrl(exp.download_url!)} download>
            Download
          </a>
        )}
        <button className="secondary" onClick={() => onOpen(clip)}>
          Open in editor
        </button>
      </div>
    </article>
  );
}
