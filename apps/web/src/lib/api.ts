export type HealthResponse = {
  status: string;
  ffmpeg: string;
  redis: string;
  jobs_succeeded?: number;
  jobs_failed?: number;
};

export type JobOut = {
  id: string;
  kind: string;
  stage: string;
  status: string;
  progress: number;
  error?: string | null;
};

export type AutopilotSummary = {
  requested: number;
  planned?: number | null;
  plan_reason: string;
  rendered: number;
  failed: number;
  render_current: number;
  render_total: number;
  render_errors: { clip_id: string; message: string }[];
  provider_used?: string | null;
  llm_error?: string | null;
  metadata_source?: string | null;
  metadata_error?: string | null;
  metadata_error_message?: string | null;
};

export type LiveStatus = {
  mode: string;
  state: string;
  behind_sec: number;
  windows_ready: number;
  windows_failed: number;
  windows_saved?: number;
};

export type Project = {
  id: string;
  title: string;
  status: string;
  source_type: string;
  created_at: string;
  source_meta?: Record<string, unknown> | null;
  autopilot: boolean;
  target_clip_count: number;
  latest_job?: JobOut | null;
  autopilot_summary?: AutopilotSummary | null;
  live?: LiveStatus | null;
  framing?: "face" | "gameplay" | "full";
  chunks?: ChunkStatus | null;
};

export type ChunkStatus = {
  planned: number;
  fetching: number;
  saved: number;
  ready: number;
  failed: number;
  total: number;
  error?: string | null;
};

export type Transcript = {
  language: string;
  model_name: string;
  words: { word: string; start: number; end: number; prob?: number | null }[];
  segments: { id?: number; start: number; end: number; text: string }[];
  proxy_url: string;
};

export type CropMode = "face" | "center" | "left" | "right" | "custom" | "full";

export type Crop = {
  mode: CropMode;
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  fallback?: string;
  t_start?: number;
  t_end?: number;
};

export type Clip = {
  id: string;
  project_id: string;
  t_start: number;
  t_end: number;
  title: string;
  status: string;
  rank: number;
  score: number;
  reason: string;
  crop?: Crop | null;
  caption_text_override?: string | null;
  post_title?: string | null;
  post_description?: string | null;
  post_body: string;
  post_hashtags: string[];
  post_meta_source?: "gemini" | "fallback" | "user" | null;
  post_meta_stale: boolean;
  stream_offset_sec?: number | null;
  latest_export?: ClipExport | null;
};

export type YouTubeStatus = {
  configured: boolean;
  connected: boolean;
  channel_title?: string | null;
  channel_label?: string | null;
  made_for_kids?: boolean | null;
  default_spacing_min: number;
};

export type PublishRow = {
  id: string;
  project_id: string;
  clip_id: string;
  title: string;
  description: string;
  privacy_status: string;
  publish_at?: string | null;
  notify_subscribers: boolean;
  made_for_kids: boolean;
  status: string;
  youtube_video_id?: string | null;
  watch_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
};

export type PublishRequest = {
  clip_ids: string[];
  privacy: "public" | "unlisted" | "private";
  schedule?: { start_at: string; every_minutes: number };
  notify_subscribers: boolean;
  made_for_kids: boolean;
  rights_confirmed: true;
};

export type PublishBatch = {
  publishes: PublishRow[];
  warnings: string[];
};

export type ClipExport = {
  id: string;
  clip_id: string;
  status: string;
  path: string;
  width?: number | null;
  height?: number | null;
  duration_sec?: number | null;
  error?: string | null;
  download_url?: string | null;
};

const PROXY_PREFIX = "/api/backend";

export function apiBase(): string {
  return PROXY_PREFIX;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${PROXY_PREFIX}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body?.error?.message || message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function fetchHealth(): Promise<HealthResponse> {
  return api<HealthResponse>("/api/v1/health");
}

export function listProjects(): Promise<Project[]> {
  return api<Project[]>("/api/v1/projects");
}

export function getProject(id: string): Promise<Project> {
  return api<Project>(`/api/v1/projects/${id}`);
}

export async function uploadProject(
  file: File,
  options: { title?: string; autopilot?: boolean; clipCount?: number; framing?: "face" | "gameplay" | "full" } = {},
): Promise<{ id: string; status: string }> {
  const form = new FormData();
  form.append("file", file);
  if (options.title) form.append("title", options.title);
  if (options.framing) form.append("framing", options.framing);
  if (options.autopilot) {
    form.append("autopilot", "true");
    if (options.clipCount) form.append("clip_count", String(options.clipCount));
  }
  return api("/api/v1/projects", { method: "POST", body: form });
}

export function createFromUrl(body: {
  url: string;
  disclaimer_accepted: boolean;
  title?: string;
  autopilot?: boolean;
  clip_count?: number;
  framing?: "face" | "gameplay" | "full";
}): Promise<{ id: string; status: string }> {
  return api("/api/v1/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function createLiveProject(body: {
  url?: string;
  folder?: string;
  disclaimer_accepted: boolean;
  title?: string;
  autopilot?: boolean;
  clip_count?: number;
  framing?: "face" | "gameplay" | "full";
}): Promise<{ id: string; status: string }> {
  return api("/api/v1/projects/live", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function updateFraming(id: string, framing: "face" | "gameplay" | "full"): Promise<Project> {
  return api<Project>(`/api/v1/projects/${id}/framing`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ framing }),
  });
}

export function stopLive(id: string): Promise<{ id: string; status: string }> {
  return api(`/api/v1/projects/${id}/live/stop`, { method: "POST" });
}

export function broadcastLabel(clip: { stream_offset_sec?: number | null; t_start: number }): string | null {
  if (clip.stream_offset_sec == null) return null;
  return `broadcast ${clock(clip.stream_offset_sec + clip.t_start)}`;
}

function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function getTranscript(id: string): Promise<Transcript> {
  return api<Transcript>(`/api/v1/projects/${id}/transcript`);
}

export function listClips(projectId: string): Promise<Clip[]> {
  return api<Clip[]>(`/api/v1/projects/${projectId}/clips`);
}

export function createClip(
  projectId: string,
  body: { t_start: number; t_end: number; title?: string },
): Promise<Clip> {
  return api<Clip>(`/api/v1/projects/${projectId}/clips`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function updateClip(
  clipId: string,
  body: {
    t_start?: number;
    t_end?: number;
    title?: string;
    status?: "accepted" | "rejected";
    crop?: { mode: CropMode; x?: number; y?: number; w?: number; h?: number };
    caption_text_override?: string;
    post_title?: string;
    post_description?: string;
    post_hashtags?: string[];
  },
): Promise<Clip> {
  return api<Clip>(`/api/v1/clips/${clipId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deleteClip(clipId: string): Promise<void> {
  return api<void>(`/api/v1/clips/${clipId}`, { method: "DELETE" });
}

export function deleteProject(projectId: string): Promise<void> {
  return api<void>(`/api/v1/projects/${projectId}`, { method: "DELETE" });
}

export function proposeClips(projectId: string): Promise<{ id: string; status: string }> {
  return api(`/api/v1/projects/${projectId}/propose`, { method: "POST" });
}

export function redraftPostText(projectId: string): Promise<{ id: string; status: string }> {
  return api(`/api/v1/projects/${projectId}/post-metadata`, { method: "POST" });
}

export function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    queued: "Queued",
    ingest: "Ingest",
    normalize: "Normalize",
    asr: "Transcribing",
    segment_score: "Ranking",
    propose_windows: "Proposing clips",
    post_metadata: "Writing post text",
    render_clips: "Rendering clips",
    export: "Exporting",
    done: "Ready",
  };
  return labels[stage] || stage || "Working";
}

export function enqueueExport(clipId: string, force = false): Promise<ClipExport> {
  const query = force ? "?force=true" : "";
  return api<ClipExport>(`/api/v1/clips/${clipId}/export${query}`, { method: "POST" });
}

export function listExports(clipId: string): Promise<ClipExport[]> {
  return api<ClipExport[]>(`/api/v1/clips/${clipId}/exports`);
}

export type LiveStreamCard = {
  video_id: string;
  url: string;
  title: string;
  channel_title: string;
  channel_id: string;
  thumbnail_url: string;
  concurrent_viewers?: number | null;
  started_at?: string | null;
  own: boolean;
};

export type LiveDiscover = {
  enabled: boolean;
  cached: boolean;
  region?: string | null;
  message?: string | null;
  streams: LiveStreamCard[];
};

export function listLiveStreams(query = "", category = ""): Promise<LiveDiscover> {
  const params = new URLSearchParams();
  if (query.trim()) params.set("q", query.trim());
  if (category) params.set("category", category);
  const suffix = params.toString() ? `?${params}` : "";
  return api<LiveDiscover>(`/api/v1/youtube/live${suffix}`);
}

export function youtubeStatus(): Promise<YouTubeStatus> {
  return api<YouTubeStatus>("/api/v1/youtube/status");
}

export function disconnectYouTube(): Promise<void> {
  return api<void>("/api/v1/youtube/disconnect", { method: "POST" });
}

export function listPublishes(projectId: string): Promise<PublishRow[]> {
  return api<PublishRow[]>(`/api/v1/projects/${projectId}/publishes`);
}

export function publishClips(projectId: string, body: PublishRequest): Promise<PublishBatch> {
  return api<PublishBatch>(`/api/v1/projects/${projectId}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function youtubeConnectHref(nextPath: string): string {
  return `${PROXY_PREFIX}/api/v1/youtube/connect?next=${encodeURIComponent(nextPath)}`;
}

export type TikTokStatus = {
  configured: boolean;
  connected: boolean;
  display_name?: string | null;
};

export type TikTokCreator = {
  nickname?: string | null;
  username?: string | null;
  privacy_level_options: string[];
  comment_disabled: boolean;
  duet_disabled: boolean;
  stitch_disabled: boolean;
  max_video_post_duration_sec: number;
  can_post: boolean;
  error_code?: string | null;
  error_message?: string | null;
};

export type TikTokPublishRow = {
  id: string;
  project_id: string;
  clip_id: string;
  caption: string;
  privacy_level: string;
  status: string;
  tiktok_post_id?: string | null;
  fail_reason?: string | null;
  error_message?: string | null;
};

export type TikTokPublishRequest = {
  clip_ids: string[];
  privacy_level: string;
  allow_comment: boolean;
  allow_duet: boolean;
  allow_stitch: boolean;
  brand_organic: boolean;
  brand_content: boolean;
  is_aigc: boolean;
  rights_confirmed: true;
  music_confirmed: true;
};

export function tiktokStatus(): Promise<TikTokStatus> {
  return api<TikTokStatus>("/api/v1/tiktok/status");
}

export function tiktokCreator(): Promise<TikTokCreator> {
  return api<TikTokCreator>("/api/v1/tiktok/creator");
}

export function disconnectTikTok(): Promise<void> {
  return api<void>("/api/v1/tiktok/disconnect", { method: "POST" });
}

export function listTikTokPublishes(projectId: string): Promise<TikTokPublishRow[]> {
  return api<TikTokPublishRow[]>(`/api/v1/projects/${projectId}/tiktok/publishes`);
}

export function publishTikTok(projectId: string, body: TikTokPublishRequest): Promise<{ publishes: TikTokPublishRow[] }> {
  return api(`/api/v1/projects/${projectId}/tiktok/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function tiktokConnectHref(nextPath: string): string {
  return `${PROXY_PREFIX}/api/v1/tiktok/connect?next=${encodeURIComponent(nextPath)}`;
}

export function fileUrl(path: string): string {
  if (path.startsWith("http")) return path;
  return `${PROXY_PREFIX}${path}`;
}
