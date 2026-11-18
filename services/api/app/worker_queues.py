"""Arq queue names shared by the API and the worker processes."""

# User-clicked exports. A separate process drains it, so an export never waits behind
# a 15-minute chunk pipeline on the default queue.
RENDER_QUEUE = "arq:render"

# YouTube and TikTok uploads. Network-bound, so they run beside clipping instead of
# waiting for a live window or a 15-minute pipeline on the default queue.
PUBLISH_QUEUE = "arq:publish"
