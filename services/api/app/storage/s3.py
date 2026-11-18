from app.errors import AppError
from app.storage.keys import safe_key


class S3Storage:
    """Unused-by-default stub. No boto3 import and no network.

    Pipeline stages need a local path for FFmpeg. Keep STORAGE_BACKEND=local
    until an object-store implementation exists.
    """

    def __init__(self, bucket: str, region: str = "", prefix: str = "", endpoint_url: str = ""):
        self.bucket = bucket
        self.region = region
        self.prefix = prefix.strip("/")
        self.endpoint_url = endpoint_url

    def put(self, key: str, data: bytes) -> None:
        self._refuse()

    def get(self, key: str) -> bytes:
        self._refuse()

    def open(self, key: str):
        self._refuse()

    def delete(self, key: str) -> None:
        self._refuse()

    def local_path(self, key: str):
        self._refuse()

    def url_for(self, key: str) -> str:
        rel = safe_key(key)
        full = f"{self.prefix}/{rel}" if self.prefix else rel
        return f"s3://{self.bucket}/{full}"

    def _refuse(self) -> None:
        raise AppError(
            "STORAGE_UNAVAILABLE",
            "STORAGE_BACKEND=s3 is a stub and is not used for pipeline I/O. Set STORAGE_BACKEND=local.",
            status_code=501,
        )
