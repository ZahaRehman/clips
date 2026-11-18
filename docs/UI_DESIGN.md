# Cliping — Interface design

**Status:** Design only. This document does not change code, routes, APIs, or pipeline behavior.  
**Date:** 17 November 2026  
**Applies to:** `apps/web` visual shell and screen structure  
**Does not override:** [CLIPPING_ARCHITECTURE.md](./CLIPPING_ARCHITECTURE.md) §3, §11, §18 (stack, env, local-first). Product rules in [plans/README.md](./plans/README.md) still hold: human approval before publish, rights disclaimer before URL ingest, job `stage` / `progress` / `error` always visible.

## Verdict

Cliping should look like a daylight cutting room on the operator’s own machine: a pale paper shell, a quiet left sidebar, and one centered bench where the work happens. The thing on screen that earns the darkest color is the 9:16 frame. Everything else stays light so the picture, the in/out marks, and the transcript read first.

The current screen is a dark document (`color-scheme: dark`, canvas `#0f1115`, cards `#1a1d24`) squeezed into a 40rem column on the home page and 56rem on a project. Upload, URL, live, projects, YouTube, TikTok, the gallery, and the editor are stacked as equal cards. That layout hides the cut.

This pass changes presentation only. Storage stays on disk. The API stays local. No cloud design dependency, no credit counter, no metric dashboard.

## Who it is for

One person at a desk, with a long video they have rights to. Five minutes ago they finished a recording or found the file. The job in this app is to cut vertical shorts, check the words and the frame, export a file, and only then post. Five minutes later they want an MP4 on disk, or a post they have already read.

The interface should feel like a lit edit bench: paper, graphite, one grease-pencil mark. Calm and exact. A tool you can leave open for an hour.

## What stays locked

- Local-first. No required cloud key to use the UI. Fonts load at build time through Next font tooling, with the current system fallback if the face is missing. Runtime does not call a font CDN.
- Same jobs, same polling, same errors. A processing project still shows `stage`, `progress`, and `error`.
- Same review rule. Manual projects accept before export. Autopilot may render local files, and posting still takes an explicit Post after the operator sees title, description, channel, and visibility.
- URL ingest still requires the rights checkbox. Upload remains the path for media the operator owns.
- Download only when the export is ready. Media URLs still go through the API file route.
- Existing capabilities stay: upload, URL, live (when enabled), project list, transcript trim, proposals, crop, captions, autopilot gallery, YouTube, TikTok.

## What the current screen does

| Surface | What it does today | What the redesign changes |
| --- | --- | --- |
| Home `apps/web/src/app/page.tsx` | Title, two connect buttons, then three stacked cards: link, live, upload, then a project list | One centered composer. Connect moves to the sidebar. Projects become the list view |
| Project `apps/web/src/app/projects/[id]/page.tsx` | Back link, connect buttons, delete, then a long scroll: live, shorts grid, preview, proposals, transcript, manual clip | One center at a time: shorts **or** the edit bench |
| Review `review-sections.tsx` | Wide source video is the large object (up to 640px). The 9:16 phone is 168×298 beside it | The 9:16 gate is the focal object. The source video is the scrubber under it |
| Chrome `globals.css` | Dark tokens, 1px solid `#2a2f3a` on every card, accent `#7aa2ff` | Light paper tokens, one mark color, sheets lift off the paper |

There is no app shell. `layout.tsx` renders `{children}` and each page builds its own top bar.

## Research

Looked at how clipping and edit tools organize a desktop web app, and at light tool-shell measurements used by products with a persistent sidebar. Sources are listed at the end. Accessed 17 November 2026.

### What is worth taking

| Source | Pattern | Use in Cliping |
| --- | --- | --- |
| Opus Clip home (captured 2026-10-22) and their dashboard changelog | Left sidebar owns projects. The home task is “bring a source in.” Generation progress stays visible after you leave the page | Sidebar + one ingest task on the home bench. Job progress stays on the project row and on the open project |
| Descript editor | The transcript is a primary surface, beside the picture, with tools in a sidebar instead of a crowded top bar | Transcript is the right half of the edit bench. Connect and delete leave the project header |
| CapCut web | Left rail for tools, picture in the center, export as its own step | Picture in the center. Export stays an action on the selected clip, after review |
| Light tool shells (Felt, Dovetail, Linear’s 2024–2026 refresh notes) | Sidebar recedes. Same family of neutrals as the canvas, or a hairline only. One accent. Active row is a tinted fill. Content is what you see when you squint | Sidebar and canvas share `--paper`. Accent is scarce |

Measurements that fit this product:

- Expanded sidebar **240px**. Labels here are short (`Projects`, `New cut`, `Connections`). 240px says the nav serves the cut. 300px and above starts to steal the bench on a 1366px laptop.
- Collapsed / drawer behavior under 900px, described below. A 64px icon rail is a desktop power-user pattern. Cliping has three destinations, so a labeled drawer is clearer than unlabeled icons.
- Nav row **36px** tall on a mouse. Touch layout uses **44px**.
- Page header inside the bench stays around **48–56px**. No full-width marketing bar.

### What we refuse

| Pattern | Where it shows up | Why it stays out |
| --- | --- | --- |
| Twelve tool chips and a “1 click” well | Opus home | Cliping has three ways in: file, link, live. A chip grid would invent products |
| What’s-new banner, samples, credit counter | Opus home | Local tool. No account balance |
| Dark inverted sidebar against a light canvas | Asana-style shells | Splits the room into two products. Sidebar and canvas are one surface |
| KPI strip, charts, rearrangeable widgets | Generic 2026 dashboard writeups | Nothing here is a metric. The north star is the clip in front of you |
| Right-hand tool rail of scenes, layers, stock, AI | Descript Season 6 | Those panels are a different product. Our tools are crop, captions, in/out, export |
| Purple-on-lavender “AI app” palette, Fira Code headlines, scroll-reveal | Generated admin theme for “video dashboard” | Fails the swap test: any AI tool could wear it. Scroll fades fight a page you poll every few seconds |

A generated palette offered primary `#6366F1`, background `#F5F3FF`, a second green CTA `#059669`, and Fira Code for headings, aimed at a real-time operations landing page. That palette is recorded here so it is not revived later. Cliping is a cutting room, and it already speaks IBM Plex.

Editorial pairings (Newsreader + Roboto, Bodoni + Public Sans, Playfair + Inter) fit magazines and luxury shops. The long text in this app is a transcript with timecodes, so the UI face stays a workhorse sans and the mono stays on times only.

## Direction

**Domain.** Cutting room, in and out, the vertical gate, the transcript as the script, takes on a rack, grease pencil, burn-in, local disk, the rights note, pipeline stage as a line on the slate.

**Color world, from that room.**

| Material | Token | Hex | Role |
| --- | --- | --- | --- |
| Plaster / script paper | `--paper` | `#F6F3EC` | Canvas and sidebar |
| Clean sheet | `--sheet` | `#FFFCF7` | Cards, menus, dialogs |
| Inset well | `--well` | `#EFEBE3` | Inputs. Darker than the sheet, so they read as places you type |
| Graphite | `--ink` | `#1C1915` | Primary text |
| Pencil | `--graphite` | `#5E584F` | Secondary and meta text |
| Hairline | `--line` | `rgba(28, 25, 21, 0.10)` | Dividers and input edges |
| Grease pencil | `--mark` | `#C4512A` | The one accent: primary button, in/out, focus |
| Mark on paper | `--mark-wash` | `rgba(196, 81, 42, 0.12)` | Selected word, active proposal |
| Highlighter | `--script` | `#F3D7A1` | The current spoken span on the script |
| Gate | `--gate` | `#141210` | The only near-black: inside the video frame |
| Take | `--take` | `#1B6B45` | Accepted, ready, connected |
| Hold | `--hold` | `#8A5A10` | Rights warning, queued, needs a look |
| Cut | `--cut` | `#A3262A` | Failed, rejected, destructive |

**Signature.** A tall 9:16 gate on pale paper, with the transcript set like a script page and the in/out drawn in grease pencil on the words. The sidebar is a rack of projects, each row a small status pip plus a title, the way reels sit on a shelf.

The signature shows up in five places:

1. The video well is `--gate`. The page around it never goes dark.
2. Selected words use `--mark-wash` and the active span uses `--script`, with `--ink` on top.
3. In and out are mono timecodes in `--mark`, sitting under the gate.
4. Project rows use a 9:16 pip (status color) instead of a generic app icon.
5. The sidebar foot says `Local` in `--graphite`. That word is the product, not a badge graphic.

**Defaults this design walks away from**

| Default | Decision |
| --- | --- |
| Dark IDE theme (current tokens) | Daylight paper. The frame stays dark |
| Indigo SaaS + lavender wash + a second green CTA | One hue family for surfaces. One accent |
| Four KPI cards across the top | No stats. The bench is the page |
| Icon rail of features | Three labeled destinations |
| Equal stacked cards | One focal object per view, everything else demoted |

**Swap test.** Replace IBM Plex with Inter, the paper with `#F5F3FF`, and the bench with a 3-column metric grid: the product disappears. The gate, the script, the mono timecodes, and the `Local` foot are what make it Cliping.

**Squint test.** Paper and sheet are close. The gate is the dark rectangle. `--mark` appears in small doses (one button, the cut marks). Borders are findable and quiet.

## Shell

```
+------------------+----------------------------------------------+
| Cliping          |                                              |
| Local            |            centered bench                    |
|                  |            (the only work surface)           |
| New cut          |                                              |
| Projects         |                                              |
| Connections      |                                              |
|                  |                                              |
| ---------------- |                                              |
| recent projects  |                                              |
|                  |                                              |
+------------------+----------------------------------------------+
  240px                         remaining viewport
```

The sidebar and the canvas share `--paper`. A 1px `--line` divides them. The sidebar does not get its own background color.

**Sidebar contents, top to bottom**

1. Wordmark `Cliping`, 18px, weight 600, tracking `-0.02em`. Under it, `Local` at 12px `--graphite`.
2. `New cut`, `Projects`, `Connections`. Text labels, 14px. Row height 36px, padding 8×10, radius 8px. Gap between rows 2px.
3. A hairline, then up to five recent projects from the list the home page already loads. Each row: 12×20 pip, title truncated, stage in `--graphite`. The open project uses `--sheet` fill and weight 600.
4. The foot stays at the bottom of the viewport. If the project list request fails, the foot shows that same error in `--cut`. It does not invent a separate health system.

Active nav row: `--sheet` fill, `--ink`, weight 600. The accent is reserved for the action and the cut, so the nav does not compete with Export.

**Bench.** The canvas is the area to the right of the sidebar. Inside it, one column, `margin-inline: auto`.

| View | Max width of the bench | Why |
| --- | --- | --- |
| New cut, Connections | 720px | A form should sit in the middle, with paper on both sides |
| Projects list | 880px | Rows can show title, stage, and error without stretching into a table |
| Edit bench, shorts grid | 1040px | 9:16 plus script, or a grid of gates |

Canvas padding: 32px from 1100px up, 24px from 900–1099, 16px below that. The bench is centered in the canvas, which is what “content in the center” means. It does not stick to the sidebar edge, and it does not run to the right edge of an ultrawide monitor.

**Header inside the bench.** One row, about 48px: page title on the left, the single primary action on the right when that view has one. No YouTube or TikTok buttons here.

**Routes.** `/` is the shell. The default center is Projects when any project exists, and New cut when the list is empty. `/projects/[id]` is the workspace, same as now. `/connections` only relocates the existing connect components. No new API.

## Screens

Each screen has one focal element. The rest is support.

### New cut

**Focal element:** the source chooser and its fields.

A segmented control, 36px tall: `Upload` · `Link` · `Live`. One form is visible. The other two are unmounted from view, so the page is not three cards.

- **Upload.** File input, the autopilot checkbox, clip count, submit. Upload is the first segment because it is the path for media the operator owns.
- **Link.** URL, optional title, clip count, autopilot checkbox, the rights checkbox, submit. Submit stays disabled until the disclaimer is checked. The rights copy stays the full sentence, set as a footnote under the fields at 14px `--graphite`, with the checkbox label itself at 15px `--ink`.
- **Live.** The current live form, including the case when live ingest is off. The off state is a sentence in the form, using the existing flag behavior, not a locked marketing card.

Primary button label matches the action: `Upload`, `Make N clips` or `Ingest URL`, `Start live`. One primary button in the form.

### Projects

**Focal element:** the list. With an empty list, the empty line and a text button `New cut` are the focus, and the shell already prefers the New cut view in that case.

Each row is a sheet:

- 12×20 pip. Color follows status: `--take` ready, `--hold` running or queued, `--cut` failed, `--graphite` anything else. The status word is also written. Color does not carry the meaning alone.
- Title, 15px weight 600.
- Stage and progress when `latest_job` exists, 13px tabular figures.
- Error text in `--cut` on the row, the same string the list shows today.
- Delete is a text button, 14px, `--cut`, with the existing confirm. It sits at the end of the row, not as a filled red block.

Rows are 56px minimum, full width of the 880px bench, gap 8px. Hover fills `--well`. The row is a link to the project. Delete is a separate button so the hit targets do not overlap.

### Project workspace

The bench header: project title (22px, weight 600), then on the same row the status, source type, stage, and percent. The progress track sits under the title, 4px tall, `--well` track, `--mark` fill, scaled with `transform: scaleX` from the left. Delete project is a quiet text button on this header, right side, secondary to the title.

While a job is running and there is nothing to review yet, that header **is** the screen. A single line names the stage in the operator’s words (the existing headline helper). The bench does not fill with skeleton cards.

**Autopilot, clips ready.** The focal element is the shorts grid. Cards keep a 9:16 video, title, post text, and the publish actions that already exist. Grid: `repeat(auto-fill, minmax(200px, 1fr))`, gap 16px, inside the 1040px bench. `Open in editor` replaces the grid with the edit bench. `Back to shorts` returns. The grid and the editor do not stack on one scroll.

**Edit bench.** This is the manual project, and the autopilot editor.

```
+---------------------------+------------------------------------+
|                           |  Transcript                        |
|      9:16 gate            |  words, in/out marks on the span   |
|      ~300px wide          |                                    |
|                           |                                    |
|  source video, 16:9       |  title, in, out                    |
|  under the gate           |  Accept   Reject   Export          |
|  crop + captions          |                                    |
+---------------------------+------------------------------------+
|  proposals / manual clips, one horizontal strip                |
+----------------------------------------------------------------+
```

- The gate column is 320px. The 9:16 frame is about 300×533, radius 16, sitting on `--gate`. This is the largest object on the page.
- The source `<video>` keeps its controls and stays the element that owns `currentTime`. It lives under the gate, full column width, max height 180px, radius 8, also on `--gate`. The small phone-in-a-phone preview goes away. The gate is the preview.
- Crop and caption fields stay under the source, in the same column, so picture decisions sit with the picture.
- The script column takes the rest. Words are 16px, line-height 1.7, on `--sheet`, padding 20px. Clicking a word still sets the range. The selected span background is `--script` with `--ink` text. The active word uses `--mark-wash`.
- In and out read as `00:12.40` in IBM Plex Mono, 13px, tabular, color `--mark`, directly under the script title. Duration sits beside them in `--graphite`.
- Accept, Reject, Export: Export is the primary button when the clip can export. Accept is primary when the clip is still a proposal. Reject and Delete stay text buttons. One filled button at a time.
- Proposals and manual clips are one strip under the bench, horizontal scroll, each item 200px: title, score or status, duration. Selected item: `--mark` hairline (1px) and `--mark-wash` fill. Rejected items drop to 55% opacity and keep the word `Rejected`.

Live capture, when the project is live, is a sheet at the top of the bench with the stop action. It does not become a second page.

Publish sheets (YouTube, TikTok) stay dialogs centered on the viewport, max width 36rem, `--sheet`, radius 12, overlay `rgba(28, 25, 21, 0.35)`. The confirm copy and the explicit Post button stay as they are.

### Connections

**Focal element:** two rows, YouTube and TikTok, using the connect components that already exist.

Each row: service name, state (`Connected` in `--take`, otherwise `--graphite`), and one button (`Connect` primary, `Disconnect` text). Helper text under the row only when the current component already explains a missing env or a failed connect. No illustration, no feature list.

## Tokens

Replace the dark block in `globals.css` (`--bg`, `--fg`, `--card`, `--accent`, and the hardcoded `#0f1115` inputs). Components read semantic tokens. Raw hex stays in the token block.

Text on the surfaces above, measured contrast (WCAG relative luminance):

| Pair | Ratio | Use |
| --- | --- | --- |
| `--ink` on `--paper` | 15.80 | Body, titles |
| `--ink` on `--sheet` | higher than on paper | Cards |
| `--graphite` on `--paper` | 6.35 | Meta, footnotes, `Local` |
| `--ink` on `--script` | 12.55 | Highlighted words |
| `--ink` on `--well` | 14.73 | Typed text |
| White on `--mark` | 4.61 | Button label, 14px weight 600. Do not lighten `--mark` |
| `--take` on `--paper` | 5.86 | Status text |
| `--hold` on `--paper` | 5.34 | Warning text |
| `--cut` on `--paper` | 6.61 | Error text |

`#8A8175` on `--paper` is 3.46 and is out. Disabled controls use `--graphite` and reduced opacity on the control chrome, with the label still at least 4.5:1. Placeholder text uses `--graphite`, not a lighter pencil.

Focus: 2px solid `--mark`, 2px offset of `--paper`. Every button, link, input, and word target has it. Hover and focus are both visible.

## Type

IBM Plex Sans for UI and transcript. IBM Plex Mono for timecodes, ids, and env names only. Both via `next/font` at build. Fallback remains `"Segoe UI", sans-serif` and `ui-monospace`.

Ratio 1.25 from a 15px body. Hierarchy uses weight and color with size.

| Role | Size | Weight | Color |
| --- | --- | --- | --- |
| Meta, nav section | 12px | 500 | `--graphite` |
| Timecode | 13px mono | 500 | `--mark` or `--ink` |
| Nav, buttons, row meta | 14px | 500–600 | `--ink` |
| Body, transcript, checkbox | 15–16px | 400 | `--ink` |
| Section label | 13px | 600 | `--graphite`, tracking `0.04em`, sentence case |
| Bench title | 22px | 600 | `--ink`, tracking `-0.02em` |
| Home wordmark | 18px | 600 | `--ink` |

Section labels are sentence case. The current uppercase 0.8rem card headers go away. Line-height 1.5 for UI copy, 1.7 for the transcript. Headings use `text-wrap: balance`. `font-variant-numeric: tabular-nums` on progress, counts, and times. Root smoothing: `-webkit-font-smoothing: antialiased`.

## Depth, radius, spacing

**One depth strategy: soft lift on sheets.** Sidebar, canvas, and list background stay flat `--paper`.

```css
--lift: 0 0 0 1px rgba(28, 25, 21, 0.06),
        0 1px 2px rgba(28, 25, 21, 0.04),
        0 12px 32px rgba(28, 25, 21, 0.04);
```

Inputs do not lift. They use `--well` and a 1px `--line`. Dividers are `--line`, not a second border color.

**Radius.** 6px inputs and buttons. 8px nav rows and video scrubber. 10px sheets. 12px dialogs. 16px the 9:16 gate. Nested radius follows `outer = inner + padding`.

**Spacing base 4.** Use 4, 8, 12, 16, 20, 24, 32, 48 only.

| Zone | Value |
| --- | --- |
| Icon-to-label | 8px |
| Inside a button | 8px 14px, height 36px (44px on the touch layout) |
| Inside a sheet | 20px |
| Between fields | 12px |
| Between bench sections | 32px |
| Between sidebar groups | 24px |

Buttons press to `scale(0.98)`. Minimum hit target 40×40 on the desktop bench, 44×44 below 900px. Word targets in the transcript keep their text size and grow the hit area with padding, without overlapping the next word’s line.

## Motion

Motion confirms a control. It does not introduce the page.

| Element | Duration | Curve | Properties |
| --- | --- | --- | --- |
| Button hover color | 150ms | `cubic-bezier(0.23, 1, 0.32, 1)` | background, color |
| Button press | 120ms | same | transform |
| Drawer | 200ms | same | transform |
| Dialog | 200ms | same | opacity, transform from `scale(0.98)` |
| Progress | 200ms | same | transform (`scaleX`) |

No scroll-triggered fades. Polling updates replace text in place. `prefers-reduced-motion: reduce` keeps color and opacity and drops transform.

## States

Every view already has these in the product. The redesign gives each a place.

| State | Where it shows |
| --- | --- |
| Empty projects | New cut is the center. Projects shows one line and a link back to New cut |
| Uploading / submitting | Primary button disabled, label `Working…`, form fields disabled |
| Pipeline running | Bench header: stage, percent, 4px track. Body copy only if there is no gallery yet |
| Job error | `--cut` text on the project row and in the bench header. The string is the API error |
| Export queued | Existing “waiting for the worker” sentence under the header, `--hold` |
| Export ready | Download link on that clip, `--take` |
| Disclaimer unchecked | Link submit disabled. The checkbox label is the explanation |
| Live disabled | Sentence inside the Live segment |
| Connect missing or failed | Row on Connections, current error text |
| Selected / rejected clip | Wash + word. Rejected also at 55% opacity |

## Responsive

| Width | Shell |
| --- | --- |
| ≥1100px | Sidebar 240px fixed. Bench centered as in the table above. Edit bench is two columns |
| 900–1099px | Sidebar 240px. Canvas padding 24px. Edit bench stacks: gate on top, script below, both inside the centered column |
| <900px | Sidebar becomes a drawer. A 44px `Menu` button at the top of the canvas opens it. Focus moves into the drawer and returns to the button on close. Bench is full width of the canvas minus 16px padding. Gate scales to the column, max 300px, centered. Dialogs inset 16px |

The drawer is the same nav, with labels. Browser back still leaves a project for the projects list. The sidebar link and the back button do the same navigation.

## Accessibility

- Contrast pairs above meet 4.5:1 for the sizes specified. `--mark` is the tight pair. Keep white labels at 14px weight 600.
- `color-scheme: light`.
- Native `<button>`, `<a>`, `<input>`, `<label>`, and `<dialog>`. The transcript words stay real buttons.
- Nav is a `<nav>` with an accessible name `Workspace`. The menu button has an accessible name `Open menu` / `Close menu`.
- Progress keeps `role="progressbar"` and the numeric value.
- Status pip plus a visible status word.
- Keyboard: tab order follows the sidebar, then the bench, top to bottom. Dialogs trap focus. Word buttons remain in the transcript order.
- No emoji icons. If an icon is added later, it is an inline SVG with a text label beside it, or `aria-hidden` when the text is present.

## How to build it later

This is the intended shape of the work, so a later pass does not redesign it again. Not part of this document’s delivery.

1. Add the light tokens and retire the dark hex values in `globals.css`, including input backgrounds hardcoded to `#0f1115`.
2. Add one shell in `layout.tsx`: sidebar, canvas, centered bench. Pages stop drawing their own top bars.
3. Split the home stacks into New cut, Projects, and Connections.
4. On the project page, show shorts or the edit bench, not both in one scroll. Make the 9:16 the preview and keep a single source video as the scrubber.
5. Leave API modules, poll interval, and copy for rights and errors as they are.

No component library. No chart library. No theme toggle in that pass. Light is the product.

## Out of scope

- Pipeline, ranking, captions, publish APIs, auth, and storage.
- A marketing homepage, sample projects, onboarding tours, or a what’s-new banner.
- Dark mode, custom themes, and a settings screen for env vars. Config stays in env, per architecture §11.
- New features dressed as empty nav items (analytics, brand kit, notifications).

## Sources

Accessed 17 November 2026.

- Opus Clip, cleaner dashboard and sidebar projects: https://opusclip.canny.io/changelog/a-cleaner-dashboard-and-new-ways-to-manage-your-clips
- Opus Clip home layout notes (capture dated 2026-10-22): https://screenrove.com/dashboards/opus-clip-product-home
- Descript editor regions: https://help.descript.com/descript-tour/the-editor-interface
- Descript Season 6 sidebar move: https://descript.canny.io/changelog/descript-season-6-release-version-91
- CapCut web editor, left rail and center preview: https://www.capcut.com/create/capcut-web-editor-template-guide
- Linear, sidebar dimmed so content leads; warmer neutrals: https://linear.app/now/behind-the-latest-design-refresh
- Light sidebar + canvas survey: https://saglitz.com/design/web-dashboards
- Sidebar width and content grid notes: https://artofstyleframe.com/blog/dashboard-design-patterns-web-apps/
- Persistent sidebar as the common tool-shell: https://cybertizeweb.com/blog/ui-ux/saas-ux-benchmark-report-2026-27/
