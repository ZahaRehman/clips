# Remotion — deferred

Phase 4 allows a Remotion spike only when ASS captions from Phase 3 are still not good enough. They are the default and they were not replaced.

Phase 3 already burns karaoke ASS (DejaVu Sans Bold, safe-zone margins, word-level timing) through FFmpeg. That path is idempotent, runs in the worker image, and does not add a Node render service. No operator report in this phase said that styling was insufficient.

FFmpeg stays the export path. A Remotion app under `apps/remotion-spike` was not added. Reopen this only for a template FFmpeg drawtext/ASS cannot express, and keep FFmpeg as the default even then.
