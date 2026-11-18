# YouTube live discovery — R&D

**Workspace:** `/Users/apple/Desktop/Cliping`  
**Research date:** 17 November 2026  
**Decision this unblocks:** Can the home page show the livestreams that are on air right now, next to the existing paste-a-URL field?  
**Status:** Implemented on the Live card as a **Live now** tab. Off until `YOUTUBE_API_KEY` is set. `GET /api/v1/youtube/live`. Capture of one broadcast the operator already chose stays in [LIVESTREAM_CLIPS_RND.md](./LIVESTREAM_CLIPS_RND.md) and [plans/PHASE_8_LIVESTREAM_CLIPS.md](./plans/PHASE_8_LIVESTREAM_CLIPS.md).

This pass did not call the API. Shapes and costs below are from the reference pages fetched the same day. An empty `eventType=live` query is legal; how many rows YouTube actually returns for it was not measured.

---

## Verdict

There is no YouTube API that returns every livestream on the site. The public method is Data API `search.list` with `type=video` and `eventType=live`. That is a ranked search, at most 50 items per call, and the default project may make **100 of those calls per day**. `pageInfo.totalResults` is an approximation. It must not be shown as “how many streams are live.”

A Live tab in Cliping can show a **sample of public broadcasts**: title, channel, thumbnail, and a watch URL, optionally sorted by concurrent viewers. Choosing a row fills the existing Live URL field. It does not start `yt-dlp`. The rights checkbox and `ALLOW_LIVE_INGEST` stay in front of capture.

`liveBroadcasts.list` is the other official list. It returns broadcasts **owned by the connected channel**, and it needs the OAuth token Cliping already requests (`youtube.readonly`). It is the right call for “your broadcast,” and the wrong call for a directory of other people’s streams.

Scraping the website, InnerTube, or `yt-dlp` search does not produce a complete catalog either, and it fights the ingest policy. Do not build those.

---

## What the product can honestly say

| Claim on the tab | True? |
| --- | --- |
| “Public live videos matching this search, highest concurrent viewers first” | Yes, for one cached page from `search.list` plus `videos.list`. |
| “Every livestream on YouTube right now” | No. Search is not a catalog. Private, unlisted, members-only, and many small public streams are absent. |
| “These are still live” | Only after a `videos.list` check. Search results lag. Drop a row whose `snippet.liveBroadcastContent` is no longer `live`. |
| “Your channel, if it is on air” | Yes, from `liveBroadcasts.list` on the connected token. That list is the channel’s own broadcasts, not the world’s. |

The home page already has **Paste a link** and a **Live** card (`apps/web/src/components/live-start.tsx`) that takes one URL or a folder. A discovery tab sits beside that card. A click writes `https://www.youtube.com/watch?v=VIDEO_ID` into the Live URL field. Start still requires the disclaimer.

---

## Options compared

| Option | Fit for Cliping | Cost | Local? | Risk |
| --- | --- | --- | --- | --- |
| **`search.list` `eventType=live`, one cached page, then `videos.list`** | The tab we can ship. Metadata only. Hands the watch URL to the Live form that already exists. | API key. Default **100 `search.list` calls/day**. `videos.list` is 1 unit in the shared 10,000-unit bucket. | No. Needs Google. Off when `YOUTUBE_API_KEY` is unset. | Incomplete by design. Easy to burn the daily search bucket with polling or deep pages. |
| **`liveBroadcasts.list` with the existing Connect token** | Pin “Your broadcast” when the operator’s own channel is live. Scope `youtube.readonly` is already requested for publish. | 1 unit per call in the shared bucket. Not the search bucket. | No. Uses the token already stored for upload. | Returns only that channel. `mine=true` cannot be combined with `broadcastStatus`. |
| **Saved channels, one `search.list` per channel** | Good later for “channels I clip,” if the operator types a few ids. | One search-bucket call per channel per refresh. Fifty channels a few times a day empties the bucket. | No | Do not sweep subscriptions on every page load. |
| **Wait, and keep paste-a-URL only** | Already shipped. Zero quota. | $0 | Yes | The operator has to know the URL. |
| **InnerTube, HTML of the Live shelf, or `yt-dlp` search** | Looks like “all live” in a demo. | $0 until it breaks | No | Undocumented, incomplete, and the same ToS problem as URL ingest, without an API contract. Not a product path. |
| **A third-party “all live streams” directory** | Another vendor’s partial index. | Paid, usually | No | Still not exhaustive. Extra ToS and a new dependency. Skip. |

---

## The calls, as documented

Base URL for all of these: `GET https://www.googleapis.com/youtube/v3/...`

### 1. Find public live videos

```text
GET /search
  part=snippet
  type=video
  eventType=live
  order=viewCount
  maxResults=25
  q=                  optional
  regionCode=         optional ISO 3166-1 alpha-2; omitted → response region defaults to US
  relevanceLanguage=  optional
  videoCategoryId=    optional
  pageToken=          do not use in the first version
```

`eventType` is legal only when `type=video`. Values are `live`, `upcoming`, and `completed`. `q` is optional, so a request with no query is valid. It is still a search, not a dump.

`order=viewCount` on an active broadcast sorts by concurrent viewers. The same page warns that any order other than `relevance` can return a smaller set. Use `viewCount` for the default tab and say the list is a sample.

Each search item the UI needs:

| Field | Use |
| --- | --- |
| `id.videoId` | Build `https://www.youtube.com/watch?v=VIDEO_ID`. This is what the Live form already accepts. |
| `snippet.title` | Card title. |
| `snippet.channelTitle` | Card subtitle. |
| `snippet.channelId` | Optional “only this channel” later. |
| `snippet.thumbnails.medium.url` | Card image. Hotlink it. Do not download it into `data/`. |
| `snippet.liveBroadcastContent` | `live`, `upcoming`, or `none`. Keep `live` only. |

`maxResults` accepts 0–50. Default if omitted is 5. One page costs one search call. The next page is another search call. `pageInfo.totalResults` can read as high as 1,000,000 and is documented as approximate. Do not render it, and do not build page buttons from it.

The only **documented** hard cap of 500 videos is the case `channelId` + `type=video` without `forMine`, `forDeveloper`, or `forContentOwner`. A general “search always stops at 500” limit is old client experience and is not in the current `search.list` reference. The first version stays on one page, so that question does not matter.

### 2. Confirm they are still live, and read the viewer count

Search does not include `concurrentViewers`. Immediately after a page comes back:

```text
GET /videos
  part=snippet,liveStreamingDetails
  id=VIDEO_ID,VIDEO_ID,...    the ids from that one search page
```

| Field | Use |
| --- | --- |
| `snippet.liveBroadcastContent` | Drop the row unless it is `live`. |
| `liveStreamingDetails.actualStartTime` | “Started …”. Absent until the broadcast has begun. |
| `liveStreamingDetails.concurrentViewers` | Show the number when present. Absent when the owner hid the count, or when nobody is watching, or after the broadcast ends. |
| `liveStreamingDetails.actualEndTime` | If set, the broadcast is over. Drop the row. |

`liveStreamingDetails` exists only for upcoming, live, or completed broadcasts. Viewer counts from this API can differ from YouTube Analytics. Do not label them as official analytics.

`statistics` is a separate part and is not required for this tab. Skip it.

### 3. The connected channel’s own broadcast

```text
GET /liveBroadcasts
  part=snippet,status
  mine=true
  maxResults=50
```

Authorization is one of `youtube.readonly`, `youtube`, or `youtube.force-ssl`. Cliping’s connect flow already asks for `youtube.readonly` (see [YOUTUBE_PUBLISH_RND.md](./YOUTUBE_PUBLISH_RND.md)). An API key cannot call this method.

The filter rule is **exactly one** of `broadcastStatus`, `id`, or `mine`. Sending `mine=true` together with `broadcastStatus=active` is an invalid combination. List with `mine=true`, then keep items whose `status.lifeCycleStatus` is `live` or `liveStarting`.

`status.lifeCycleStatus` values: `complete`, `created`, `live`, `liveStarting`, `ready`, `revoked`, `testStarting`, `testing`. `testing` is visible to the partner only. Do not offer a testing broadcast as a public card.

`liveStreams.list` is the ingest stream (the RTMP object), not the public video. It is also `mine`-scoped. Do not use it for the tab.

---

## Quota

From the quota calculator, fetched 17 November 2026. This matches the correction already in the publish research. Older posts that charge `search.list` as 100 units inside the 10,000-unit pool are out of date.

| Bucket | Default per day | What a Live tab spends |
| --- | --- | --- |
| `search.list` | **100 calls**. Each call costs 1 in that bucket, including a call that errors. | One search page. A second page is a second call. |
| Everything else, including `videos.list`, `liveBroadcasts.list`, `videoCategories.list` | **10,000 units** | 1 unit per call. Enriching one page is 1 unit. |
| `videos.insert` | 100 calls, separate | Unchanged. Discovery must not touch upload. |

Quotas reset at **midnight Pacific Time**.

A browser that calls search on every visit, or a poll every minute, spends the day in about two hours (`100` calls). Cache on the API process, keyed by query, region, language, category, and order.

| Cache | Suggested hold | Search calls per day |
| --- | --- | --- |
| Default tab, no query, `order=viewCount`, one region | 30–60 minutes | about 24–48 |
| A query the operator just typed | 10 minutes, same key shared by every refresh | 1 per distinct query per hold |
| `videoCategories.list` for a category menu | 1 day | 0 search calls. 1 shared unit. |
| Connected channel’s `liveBroadcasts.list` | 60 seconds is enough | 0 search calls |

Leave headroom. A default refresh every 15 minutes is about 96 search calls and leaves almost nothing for typed queries. Prefer 30–60 minutes for the default, and spend the rest on searches the operator actually types. Do not prefetch page 2.

The key stays in the API environment. It is not a `NEXT_PUBLIC_` value. Restrict it to the YouTube Data API. The web app calls Cliping, and Cliping calls Google.

When the key is unset, the tab explains that discovery is off and the URL field still works. That matches the project rule that cloud keys are optional.

---

## Recommendation

Ship this only as a metadata tab, behind `YOUTUBE_API_KEY`. Do not open a new capture phase for it. Do not change `yt-dlp` argv.

**Do**

1. `GET` on our API, for example `/api/v1/youtube/live`, with optional `q`, `region`, and `category`. The handler runs the two Google calls above, drops rows that are not `live`, and returns the cache.
2. Cards show thumbnail, title, channel, viewer count when present, and started-at when present. The button label is “Use this stream,” and it only fills the Live URL.
3. On-screen sentence: “A ranked sample of public live videos. Not every stream on YouTube.”
4. If Connect has a token, a separate small call lists that channel’s `live` / `liveStarting` broadcasts and pins them. Failure of that call does not blank the public sample.
5. `403 quotaExceeded` on search shows “Live search is exhausted until midnight Pacific” and leaves paste-a-URL working.

**Do not**

- Auto-start live capture from a card. Rights and `ALLOW_LIVE_INGEST` stay on the existing button.
- Page past the first 25–50.
- Poll Google from the browser, or poll the Cliping route faster than the cache.
- Call `search.list` once per subscribed channel on load.
- Scrape `youtube.com`, call InnerTube, or shell out to `yt-dlp` to build the list.
- Treat `liveBroadcasts` or `liveStreams` as a public directory.
- Display `totalResults` as a count of all live streams.
- Add a download scope. This API still does not return media bytes. Recording stays the Phase 8 path, with the same disclaimer as today.

**Categories.** If the tab gets a dropdown, fill it from `videoCategories.list` (`regionCode` set, cache a day). Do not hardcode ids. Which categories are assignable depends on the region.

**Subscriptions.** `subscriptions.list` can list channels the connected account follows, at 1 unit per page of 50. Turning each of those into “is live?” is a `search.list` per channel. That is a later, explicit “check these channels” button with a cap (on the order of 10 channels), not the default tab.

---

## Rights

Showing public title, channel, thumbnail, and watch URL through the Data API is the supported way to display public videos. It is not permission to record them.

Starting capture from a discovered URL is the same copyright and YouTube Terms question as pasting that URL. Architecture §1 still applies. The tab does not weaken the disclaimer, and it does not add cookies, a members-only path, or a DRM bypass.

---

## Sources

Accessed **17 November 2026**.

- [Search: list](https://developers.google.com/youtube/v3/docs/search/list) — `eventType`, `order=viewCount` for live concurrent viewers, `maxResults` 0–50, optional `q`, default region behavior, quota line “100 calls per day” at 1 unit in the Search Queries bucket. Page content fetched this date; the linked search resource was last updated 2026-11-12 UTC.
- [Search resource](https://developers.google.com/youtube/v3/docs/search) — `id.videoId`, `snippet.liveBroadcastContent` (`upcoming`, `live`, `none`), thumbnail keys. Last updated 2026-11-12 UTC.
- [Videos resource](https://developers.google.com/youtube/v3/docs/videos) — `liveStreamingDetails.actualStartTime`, `actualEndTime`, `concurrentViewers` (present only with current viewers and a visible count; not tracked after the broadcast ends).
- [Quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost) — separate 100-call buckets for `search.list` and `videos.insert`; 10,000 units for other methods; `videos.list` and `liveBroadcasts.list` cost 1; reset at midnight Pacific.
- [LiveBroadcasts: list](https://developers.google.com/youtube/v3/live/docs/liveBroadcasts/list) — OAuth scopes, exactly one of `broadcastStatus` / `id` / `mine`.
- [liveBroadcast resource](https://developers.google.com/youtube/v3/live/docs/liveBroadcasts#status.lifeCycleStatus) — `lifeCycleStatus` values, including `live` and `liveStarting`. `testing` is partner-visible.
- This repo: `apps/web/src/app/page.tsx`, `apps/web/src/components/live-start.tsx`, [YOUTUBE_PUBLISH_RND.md](./YOUTUBE_PUBLISH_RND.md) quota section, [LIVESTREAM_CLIPS_RND.md](./LIVESTREAM_CLIPS_RND.md).
