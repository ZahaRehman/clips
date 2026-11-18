from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: str = "./data"
    database_url: str = "sqlite+aiosqlite:///./data/cliping.db"
    redis_url: str = "redis://localhost:6379/0"
    api_token: str = ""
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_region: str = ""
    s3_prefix: str = ""
    s3_endpoint_url: str = ""
    min_free_bytes: int = 1073741824
    cleanup_max_age_days: int = 7
    cleanup_delete_proxy: bool = False
    watermark_text: str = ""
    watermark_image: str = ""
    cors_origins: str = "http://localhost:3000"
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    whisper_model: str = "medium"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_model_dir: str = "./models/whisper"

    max_source_duration_sec: int = 43200
    max_source_bytes: int = 8589934592
    export_width: int = 1080
    export_height: int = 1920
    full_export_width: int = 1920
    full_export_height: int = 1080
    # Encode speed. `medium` and 60 fps sources made one 35 s short take ~135 s on a 6-core laptop.
    export_preset: str = "veryfast"
    # 18 is visibly sharper than 21 at the same encode time (SSIM 0.991 vs 0.988, files ~50% bigger).
    export_crf: int = 18
    # Highest source height to download. 1440 makes vertical shorts sharper; 1080 is faster.
    source_max_height: int = 1440
    export_audio_bitrate: str = "192k"
    export_max_fps: float = 30
    proxy_preset: str = "veryfast"
    proxy_max_fps: float = 30
    # Finished videos up to this length run as one file; longer ones are split into LIVE_CHUNK_SEC ranges.
    vod_chunk_threshold_sec: float = 1800
    # Most shorts rendered per live window. 0 = no cap beyond the project's clip count.
    live_window_max_clips: int = 3
    clip_min_sec: float = 5
    clip_max_sec: float = 180
    target_clip_count: int = 8
    allow_url_ingest: bool = False
    allow_live_ingest: bool = False
    live_chunk_sec: float = 300
    live_chunk_overlap_sec: float = 20
    live_edge_lag_sec: float = 20
    live_max_hours: float = 8
    live_folder_root: str = ""
    live_stall_sec: float = 180
    live_folder_stable_sec: float = 10
    yt_dlp_path: str = "yt-dlp"
    llm_provider: str = "none"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.1:8b"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    llm_timeout_sec: float = 90
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_fallback_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_sec: float = 90
    gemini_max_transcript_chars: int = 300000
    channel_context: str = ""
    autopilot_clip_count: int = 10
    autopilot_clip_min_sec: float = 20
    autopilot_clip_max_sec: float = 59
    autopilot_target_sec: int = 35
    worker_job_timeout_sec: int = 21600
    keyword_boosts: str = "tip,mistake,secret,why,how,lesson"
    fonts_dir: str = ""
    asr_aligner: str = "none"
    youtube_api_key: str = ""
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://localhost:3000/api/backend/api/v1/youtube/callback"
    youtube_category_id: str = "22"
    youtube_default_spacing_min: int = 180
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = "http://localhost:3001/api/backend/api/v1/tiktok/callback"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def sync_database_url(self) -> str:
        """Worker engine URL. Async drivers stay on the API; the worker uses a sync driver."""
        url = self.database_url
        if url.startswith("sqlite+aiosqlite://"):
            return "sqlite://" + url.removeprefix("sqlite+aiosqlite://")
        if "+asyncpg" in url:
            return url.replace("+asyncpg", "+psycopg", 1)
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
