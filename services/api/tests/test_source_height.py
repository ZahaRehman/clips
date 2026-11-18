"""1440p sources: tried first, SDR only, plain https for range downloads, 1080p fallback."""

import unittest
from pathlib import Path

from app.pipeline.url_ingest import DOWNLOAD_FORMAT, download_format, yt_dlp_argv
from app.vod.argv import SECTION_FORMAT, section_argv


class SourceHeightTests(unittest.TestCase):
    def test_1080_keeps_the_old_format(self) -> None:
        self.assertEqual(download_format(1080), DOWNLOAD_FORMAT)

    def test_1440_is_tried_first_then_falls_back(self) -> None:
        fmt = download_format(1440)
        self.assertTrue(fmt.startswith("bv*[height<=1440][height>1080][dynamic_range=?SDR]"))
        self.assertIn("vp0?9", fmt)
        self.assertTrue(fmt.endswith(DOWNLOAD_FORMAT))
        self.assertNotIn("protocol", fmt)

    def test_sections_use_https_streams(self) -> None:
        fmt = download_format(1440, fallback=SECTION_FORMAT, sections=True)
        self.assertIn("[protocol^=http]", fmt)
        self.assertTrue(fmt.endswith(SECTION_FORMAT))
        argv = section_argv("https://example.com/v", Path("/tmp/o.mp4"), 0, 300)
        self.assertIn("[protocol^=http]", argv[argv.index("-f") + 1])

    def test_default_setting_is_1440(self) -> None:
        argv = yt_dlp_argv("https://example.com/v", Path("/tmp/o.mp4"))
        self.assertIn("height<=1440", argv[argv.index("-f") + 1])


if __name__ == "__main__":
    unittest.main()
