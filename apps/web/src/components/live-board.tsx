"use client";

import { useEffect, useState, type FormEvent } from "react";
import { FramingField, type Framing } from "@/components/framing-field";
import { createLiveProject, listLiveStreams, type LiveStreamCard } from "@/lib/api";

const GAMING = "20";

export function LiveBoard() {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [streams, setStreams] = useState<LiveStreamCard[]>([]);
  const [enabled, setEnabled] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [disclaimer, setDisclaimer] = useState(false);
  const [autopilot, setAutopilot] = useState(true);
  const [framing, setFraming] = useState<Framing>("face");
  const [clipCount, setClipCount] = useState(10);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [errorFor, setErrorFor] = useState<string | null>(null);

  useEffect(() => {
    let cancel = false;
    setLoading(true);
    setError(null);
    listLiveStreams(query, category)
      .then((page) => {
        if (cancel) return;
        setEnabled(page.enabled);
        setStreams(page.streams);
        setNotice(page.enabled ? null : page.message || null);
        setSelected((current) =>
          current && page.streams.some((stream) => stream.video_id === current)
            ? current
            : page.streams[0]?.video_id ?? null,
        );
      })
      .catch((err: unknown) => {
        if (cancel) return;
        setStreams([]);
        setNotice(null);
        setError(err instanceof Error ? err.message : "Live search failed");
      })
      .finally(() => {
        if (!cancel) setLoading(false);
      });
    return () => {
      cancel = true;
    };
  }, [query, category]);

  const featured = streams.find((stream) => stream.video_id === selected) ?? streams[0] ?? null;
  const rail = streams.filter((stream) => stream.video_id !== featured?.video_id).slice(0, 4);

  function onSearch(event: FormEvent) {
    event.preventDefault();
    setQuery(draft.trim());
  }

  async function start(stream: LiveStreamCard) {
    if (!disclaimer) {
      setErrorFor(stream.video_id);
      setError("Accept the rights disclaimer before starting a live capture.");
      return;
    }
    setBusyId(stream.video_id);
    setErrorFor(stream.video_id);
    setError(null);
    try {
      const created = await createLiveProject({
        url: stream.url,
        disclaimer_accepted: true,
        autopilot,
        clip_count: autopilot ? clipCount : undefined,
        framing,
      });
      window.location.href = `/projects/${created.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Live capture failed");
      setBusyId(null);
    }
  }

  return (
    <main className="live-page">
      <h1>Live now</h1>
      <p className="subtitle">
        Pick a public stream from this sample. Clips show up while it is still on, about one chunk behind. This is not
        every livestream on YouTube.
      </p>

      <form className="live-tools" onSubmit={onSearch}>
        <input
          type="text"
          placeholder="Search live videos"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={loading}
        />
        <div className="live-chips">
          <button type="button" className={category === "" ? undefined : "secondary"} onClick={() => setCategory("")}>
            Everything
          </button>
          <button
            type="button"
            className={category === GAMING ? undefined : "secondary"}
            onClick={() => setCategory(GAMING)}
          >
            Gaming
          </button>
        </div>
      </form>

      <label className="check">
        <input type="checkbox" checked={disclaimer} onChange={(event) => setDisclaimer(event.target.checked)} />
        <span>
          I have rights to this media. I understand downloading from YouTube can violate that site’s terms, and I accept
          responsibility for copyright.
        </span>
      </label>
      <div className="row-inputs">
        <label>
          Clips
          <input
            type="number"
            min={1}
            max={30}
            value={clipCount}
            onChange={(event) => setClipCount(Math.max(1, Math.min(30, Number(event.target.value) || 1)))}
          />
        </label>
        <label className="check">
          <input type="checkbox" checked={autopilot} onChange={(event) => setAutopilot(event.target.checked)} />
          <span>Autopilot for the stream you start</span>
        </label>
        <FramingField value={framing} disabled={busyId !== null} onChange={setFraming} />
      </div>

      {notice && <p className="muted">{notice}</p>}
      {loading && <p className="muted">Loading live videos…</p>}

      {!loading && enabled && streams.length === 0 && !error && (
        <p className="muted">No public live videos came back for this search.</p>
      )}

      {featured && (
        <section>
          <h2 className="live-heading">
            Trending in this sample <span className="badge">{streams.length} shown</span>
          </h2>
          <div className="live-stage">
            <div>
              <div className="live-player">
                {embedSrc(featured.video_id) ? (
                  <iframe
                    src={embedSrc(featured.video_id) || undefined}
                    title={featured.title || "Live video"}
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowFullScreen
                  />
                ) : null}
              </div>
              <div className="live-feature-meta">
                <div>
                  <strong>{featured.title || featured.video_id}</strong>
                  <div className="meta">
                    {featured.channel_title || "YouTube"}
                    {featured.own ? " · Your broadcast" : ""}
                    {watching(featured.concurrent_viewers) ? ` · ${watching(featured.concurrent_viewers)}` : ""}
                  </div>
                </div>
                <StartControl
                  stream={featured}
                  busyId={busyId}
                  error={errorFor === featured.video_id ? error : null}
                  onStart={start}
                />
              </div>
            </div>
            <div className="live-rail">
              {rail.map((stream) => (
                <button
                  key={stream.video_id}
                  type="button"
                  className="secondary live-pick"
                  onClick={() => setSelected(stream.video_id)}
                >
                  <Thumb stream={stream} />
                  <span>
                    <strong>{stream.channel_title || "YouTube"}</strong>
                    <span className="meta">{stream.title}</span>
                    {watching(stream.concurrent_viewers) && (
                      <span className="meta">{watching(stream.concurrent_viewers)}</span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </section>
      )}

      {streams.length > 0 && (
        <section>
          <h2 className="live-heading">Live streams</h2>
          <p className="muted">Highest concurrent viewers in this sample, first.</p>
          <div className="live-grid">
            {streams.map((stream) => (
              <article key={stream.video_id} className="live-card">
                <button type="button" className="live-thumb-btn" onClick={() => setSelected(stream.video_id)}>
                  <Thumb stream={stream} />
                </button>
                <div className="live-card-title">{stream.title || stream.video_id}</div>
                <div className="meta">
                  {stream.channel_title || "YouTube"}
                  {stream.own ? " · Your broadcast" : ""}
                </div>
                <StartControl
                  stream={stream}
                  busyId={busyId}
                  error={errorFor === stream.video_id ? error : null}
                  onStart={start}
                />
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function StartControl({
  stream,
  busyId,
  error,
  onStart,
}: {
  stream: LiveStreamCard;
  busyId: string | null;
  error: string | null;
  onStart: (stream: LiveStreamCard) => void;
}) {
  return (
    <div className="live-start-col">
      <button
        type="button"
        className="live-start"
        disabled={busyId !== null}
        onClick={() => onStart(stream)}
      >
        {busyId === stream.video_id ? "Starting…" : "Start clipping"}
      </button>
      {error && (
        <p className="bad small live-start-note" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function Thumb({ stream }: { stream: LiveStreamCard }) {
  return (
    <span className="live-thumb">
      {stream.thumbnail_url ? <img src={stream.thumbnail_url} alt="" /> : <span className="live-thumb-empty" />}
      <span className="live-badge">LIVE</span>
      {watching(stream.concurrent_viewers) && (
        <span className="live-viewers">{watching(stream.concurrent_viewers)}</span>
      )}
    </span>
  );
}

function watching(value?: number | null): string | null {
  if (value == null) return null;
  if (value >= 10000) return `${Math.round(value / 1000)}K watching`;
  return `${value.toLocaleString()} watching`;
}

function embedSrc(videoId: string): string | null {
  if (!/^[A-Za-z0-9_-]{6,32}$/.test(videoId)) return null;
  return `https://www.youtube-nocookie.com/embed/${videoId}`;
}
