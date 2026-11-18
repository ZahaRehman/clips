"use client";

import { useMemo, useState } from "react";
import type { PublishItem } from "@/components/youtube-publish";
import {
  tiktokConnectHref,
  tiktokCreator,
  type TikTokCreator,
  type TikTokPublishRequest,
  type TikTokPublishRow,
  type TikTokStatus,
} from "@/lib/api";

type Props = {
  status: TikTokStatus;
  items: PublishItem[];
  publishes: TikTokPublishRow[];
  busy: boolean;
  onPost: (body: TikTokPublishRequest) => Promise<void>;
};

const ACTIVE = new Set(["queued", "uploading", "processing"]);

const PRIVACY_LABEL: Record<string, string> = {
  PUBLIC_TO_EVERYONE: "Everyone",
  MUTUAL_FOLLOW_FRIENDS: "Friends",
  FOLLOWER_OF_CREATOR: "Followers",
  SELF_ONLY: "Only me",
};

const MUSIC = "By posting, you agree to TikTok's Music Usage Confirmation.";
const MUSIC_BRANDED = "By posting, you agree to TikTok's Branded Content Policy and Music Usage Confirmation.";

export function TikTokBar({ status, items, publishes, busy, onPost }: Props) {
  const [open, setOpen] = useState(false);
  const [creator, setCreator] = useState<TikTokCreator | null>(null);
  const [loading, setLoading] = useState(false);
  const [privacy, setPrivacy] = useState("");
  const [allowComment, setAllowComment] = useState(false);
  const [allowDuet, setAllowDuet] = useState(false);
  const [allowStitch, setAllowStitch] = useState(false);
  const [commercial, setCommercial] = useState(false);
  const [brandOrganic, setBrandOrganic] = useState(false);
  const [brandContent, setBrandContent] = useState(false);
  const [aigc, setAigc] = useState(false);
  const [music, setMusic] = useState(false);
  const [rights, setRights] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = items.filter((item) => item.picked && item.selectable);
  const dirty = selected.some((item) => item.dirty);
  const running = publishes.some((row) => ACTIVE.has(row.status));
  const maxDuration = creator?.max_video_post_duration_sec ?? 180;
  const tooLong = selected.filter((item) => item.duration > 180 || item.duration > maxDuration);
  const captions = useMemo(() => selected.map((item) => captionOf(item.title, item.description)), [selected]);

  function connect() {
    const next = window.location.pathname.startsWith("/projects/") ? window.location.pathname : "/";
    window.location.href = tiktokConnectHref(next);
  }

  async function openSheet() {
    setError(null);
    setPrivacy("");
    setAllowComment(false);
    setAllowDuet(false);
    setAllowStitch(false);
    setCommercial(false);
    setBrandOrganic(false);
    setBrandContent(false);
    setAigc(false);
    setMusic(false);
    setRights(false);
    setOpen(true);
    setLoading(true);
    try {
      setCreator(await tiktokCreator());
    } catch (err) {
      setCreator(null);
      setError(err instanceof Error ? err.message : "Could not load the TikTok account");
    } finally {
      setLoading(false);
    }
  }

  async function send() {
    if (!creator?.can_post || !privacy) return;
    setError(null);
    try {
      await onPost({
        clip_ids: selected.map((item) => item.id),
        privacy_level: privacy,
        allow_comment: allowComment,
        allow_duet: allowDuet,
        allow_stitch: allowStitch,
        brand_organic: commercial && brandOrganic,
        brand_content: commercial && brandContent && privacy !== "SELF_ONLY",
        is_aigc: aigc,
        rights_confirmed: true,
        music_confirmed: true,
      });
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Post failed");
    }
  }

  const disclosureIncomplete = commercial && !brandOrganic && !(brandContent && privacy !== "SELF_ONLY");
  const blocked =
    busy ||
    running ||
    dirty ||
    loading ||
    selected.length === 0 ||
    tooLong.length > 0 ||
    !creator?.can_post ||
    !privacy ||
    !music ||
    !rights ||
    disclosureIncomplete;

  return (
    <div className="publish-bar">
      <h3>TikTok</h3>
      {dirty && <p className="banner warn">Save title and description on the selected clips first.</p>}
      {running && <p className="muted small">A TikTok batch is uploading. This button stays off until it finishes.</p>}
      {tooLong.length > 0 && (
        <p className="banner warn">A selected clip is longer than TikTok will take ({Math.min(180, maxDuration)}s).</p>
      )}
      {error && <p className="bad small">{error}</p>}
      <div className="actions">
        {status.connected ? (
          <button type="button" disabled={busy || running || dirty || selected.length === 0} onClick={() => void openSheet()}>
            Post {selected.length} clip{selected.length === 1 ? "" : "s"} to TikTok
          </button>
        ) : (
          <button type="button" onClick={connect}>
            Connect TikTok
          </button>
        )}
        <span className="muted small">{status.connected ? status.display_name || "TikTok connected" : "Not connected"}</span>
      </div>
      {open && (
        <div className="sheet-backdrop" role="presentation" onClick={() => setOpen(false)}>
          <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="tiktok-title" onClick={(e) => e.stopPropagation()}>
            <h2 id="tiktok-title">Post {selected.length} clips to TikTok</h2>
            {loading && <p className="muted">Loading the TikTok account…</p>}
            {creator && !creator.can_post && <p className="bad">{creator.error_message || "TikTok will not take posts right now."}</p>}
            {creator?.can_post && (
              <>
                <p>
                  Account: <strong>{creator.nickname || "TikTok"}</strong>
                  {creator.username ? <span className="muted"> @{creator.username}</span> : null}
                </p>
                <label className="field">
                  Privacy
                  <select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                    <option value="">Choose</option>
                    {creator.privacy_level_options.map((option) => (
                      <option key={option} value={option}>
                        {PRIVACY_LABEL[option] || option}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="check">
                  <input type="checkbox" checked={allowComment} disabled={creator.comment_disabled} onChange={(e) => setAllowComment(e.target.checked)} />
                  <span>{creator.comment_disabled ? "Comments are off for this account" : "Allow comments"}</span>
                </label>
                <label className="check">
                  <input type="checkbox" checked={allowDuet} disabled={creator.duet_disabled} onChange={(e) => setAllowDuet(e.target.checked)} />
                  <span>{creator.duet_disabled ? "Duets are off for this account" : "Allow duet"}</span>
                </label>
                <label className="check">
                  <input type="checkbox" checked={allowStitch} disabled={creator.stitch_disabled} onChange={(e) => setAllowStitch(e.target.checked)} />
                  <span>{creator.stitch_disabled ? "Stitch is off for this account" : "Allow stitch"}</span>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={commercial}
                    onChange={(e) => {
                      setCommercial(e.target.checked);
                      if (!e.target.checked) {
                        setBrandOrganic(false);
                        setBrandContent(false);
                      }
                    }}
                  />
                  <span>Disclose commercial content</span>
                </label>
                {commercial && (
                  <>
                    <label className="check">
                      <input type="checkbox" checked={brandOrganic} onChange={(e) => setBrandOrganic(e.target.checked)} />
                      <span>Your brand (promotional content)</span>
                    </label>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={brandContent && privacy !== "SELF_ONLY"}
                        disabled={privacy === "SELF_ONLY"}
                        onChange={(e) => setBrandContent(e.target.checked)}
                      />
                      <span>Branded content (paid partnership). Not available with Only me.</span>
                    </label>
                    {disclosureIncomplete && <p className="banner warn">Choose Your brand, Branded content, or both.</p>}
                  </>
                )}
                <label className="check">
                  <input type="checkbox" checked={aigc} onChange={(e) => setAigc(e.target.checked)} />
                  <span>Label as AI-generated</span>
                </label>
                <ul className="confirm-list">
                  {selected.map((item, index) => (
                    <li key={item.id}>
                      <strong>{item.title || "(no title)"}</strong>
                      {item.duration > 60 && <span className="badge warn">Over 60s</span>}
                      <pre>{captions[index] || "(empty caption)"}</pre>
                    </li>
                  ))}
                </ul>
                <p className="muted small">After you post, it can take a few minutes before the posts show in the TikTok app.</p>
                <label className="check">
                  <input type="checkbox" checked={music} onChange={(e) => setMusic(e.target.checked)} />
                  <span>{brandContent && privacy !== "SELF_ONLY" ? MUSIC_BRANDED : MUSIC}</span>
                </label>
                <label className="check">
                  <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} />
                  <span>I own these videos or have permission to post them.</span>
                </label>
              </>
            )}
            <div className="actions">
              <button type="button" disabled={blocked} onClick={() => void send()}>
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

function captionOf(title: string, description: string): string {
  const head = title.trim();
  const body = description.trim();
  if (head && body) return `${head}\n${body}`;
  return head || body;
}
