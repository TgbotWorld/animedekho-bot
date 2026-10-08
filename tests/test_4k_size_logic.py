import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock

from utils.anime_match import (
    is_native_4k,
    is_enhanced_1080p,
    parse_size_mb,
    is_around_1_to_2_gb,
    is_4k_satisfying,
    qualities_match,
)


class Test4KSizeLogic(unittest.TestCase):
    def test_native_4k_detection(self):
        self.assertTrue(is_native_4k("4K"))
        self.assertTrue(is_native_4k("2160p"))
        self.assertTrue(is_native_4k("4K UHD"))
        self.assertTrue(is_native_4k("UHD"))
        self.assertFalse(is_native_4k("1080p HQ x265"))
        self.assertFalse(is_native_4k("1080p"))
        self.assertFalse(is_native_4k("720p"))

    def test_enhanced_1080p_detection(self):
        self.assertTrue(is_enhanced_1080p("1080p HQ x265"))
        self.assertTrue(is_enhanced_1080p("1080p HQ"))
        self.assertTrue(is_enhanced_1080p("1080p x265"))
        self.assertTrue(is_enhanced_1080p("1080p 10-Bit"))
        self.assertTrue(is_enhanced_1080p("1080p HEVC"))
        self.assertTrue(is_enhanced_1080p("1080p 10bit"))
        self.assertFalse(is_enhanced_1080p("1080p"))
        self.assertFalse(is_enhanced_1080p("4K"))
        self.assertFalse(is_enhanced_1080p("720p HEVC"))

    def test_parse_size_mb(self):
        self.assertAlmostEqual(parse_size_mb("1.45 GB"), 1484.8, places=1)
        self.assertAlmostEqual(parse_size_mb("850 MB"), 850.0, places=1)
        self.assertAlmostEqual(parse_size_mb("2.1 GiB"), 2150.4, places=1)
        self.assertAlmostEqual(parse_size_mb(1024 * 1024 * 1500), 1500.0, places=1)
        self.assertIsNone(parse_size_mb(None))
        self.assertIsNone(parse_size_mb("unknown"))

    def test_is_around_1_to_2_gb(self):
        # Within ~850 MB to ~2600 MB
        self.assertTrue(is_around_1_to_2_gb("1.4 GB"))
        self.assertTrue(is_around_1_to_2_gb("1 GB"))
        self.assertTrue(is_around_1_to_2_gb("2 GB"))
        self.assertTrue(is_around_1_to_2_gb("900 MB"))
        self.assertTrue(is_around_1_to_2_gb("2.2 GB"))
        self.assertTrue(is_around_1_to_2_gb(1024 * 1024 * 1200)) # ~1.2 GB in bytes

        # Out of range
        self.assertFalse(is_around_1_to_2_gb("350 MB"))
        self.assertFalse(is_around_1_to_2_gb("500 MB"))
        self.assertFalse(is_around_1_to_2_gb("800 MB"))
        self.assertFalse(is_around_1_to_2_gb("3.5 GB"))
        self.assertFalse(is_around_1_to_2_gb("4 GB"))
        self.assertFalse(is_around_1_to_2_gb(None))
        self.assertFalse(is_around_1_to_2_gb(1024 * 1024 * 300))

    def test_is_4k_satisfying(self):
        # Native 4K always satisfies
        self.assertTrue(is_4k_satisfying("4K"))
        self.assertTrue(is_4k_satisfying("2160p"))
        self.assertTrue(is_4k_satisfying("4K UHD", size="300 MB"))

        # Enhanced 1080p satisfies ONLY around 1 to 2 GB
        self.assertTrue(is_4k_satisfying("1080p HQ x265", size="1.4 GB"))
        self.assertTrue(is_4k_satisfying("1080p HQ x265", size="1.8 GB"))
        self.assertTrue(is_4k_satisfying("1080p HQ x265 [1.5 GB]"))
        self.assertTrue(is_4k_satisfying("1080p 10-Bit", size=1024 * 1024 * 1400))

        # Enhanced 1080p does NOT satisfy if size is small, large, or missing
        self.assertFalse(is_4k_satisfying("1080p HQ x265")) # No size
        self.assertFalse(is_4k_satisfying("1080p HQ x265", size="400 MB"))
        self.assertFalse(is_4k_satisfying("1080p HQ x265", size="600 MB"))
        self.assertFalse(is_4k_satisfying("1080p HQ x265", size="3.5 GB"))
        self.assertFalse(is_4k_satisfying("1080p HQ"))
        self.assertFalse(is_4k_satisfying("1080p", size="1.5 GB")) # Plain 1080p not enhanced

    def test_qualities_match_4k(self):
        # 4K requested
        self.assertTrue(qualities_match("4K", "4K"))
        self.assertTrue(qualities_match("4K", "2160p"))
        self.assertTrue(qualities_match("4K", "1080p HQ x265", size="1.5 GB"))
        self.assertFalse(qualities_match("4K", "1080p HQ x265", size="400 MB"))
        self.assertFalse(qualities_match("4K", "1080p HQ x265"))
        self.assertFalse(qualities_match("4K", "1080p", size="1.5 GB"))
        self.assertFalse(qualities_match("4K", "720p"))

    def test_database_cached_file_4k_filtering(self):
        from bot.database import Database

        db = Database.__new__(Database)
        db.files = MagicMock()

        # Mock documents
        doc_4k = {"quality": "4K", "file_size": 2_000_000_000, "file_id": "id_4k"}
        doc_1080hq_1_5gb = {"quality": "1080p HQ x265", "file_size": 1_500_000_000, "file_id": "id_hq_good"}
        doc_1080hq_300mb = {"quality": "1080p HQ x265", "file_size": 300_000_000, "file_id": "id_hq_small"}
        doc_plain_1080p = {"quality": "1080p", "file_size": 1_500_000_000, "file_id": "id_plain_1080"}

        async def _run_test():
            # Test 1: Native 4K matched
            async def _cursor_generator_1():
                yield doc_4k
            db.files.find = MagicMock(return_value=_cursor_generator_1())
            res = await db.find_cached_file("test-slug", "S1E01", "4K")
            self.assertEqual(res["file_id"], "id_4k")

            # Test 2: 1080p HQ x265 with ~1.5 GB satisfies 4K
            async def _cursor_generator_2():
                yield doc_1080hq_1_5gb
            db.files.find = MagicMock(return_value=_cursor_generator_2())
            res = await db.find_cached_file("test-slug", "S1E01", "4K")
            self.assertEqual(res["file_id"], "id_hq_good")

            # Test 3: 1080p HQ x265 with 300 MB does NOT satisfy 4K
            async def _cursor_generator_3():
                yield doc_1080hq_300mb
            db.files.find = MagicMock(return_value=_cursor_generator_3())
            res = await db.find_cached_file("test-slug", "S1E01", "4K")
            self.assertIsNone(res)

            # Test 4: Plain 1080p does NOT satisfy 4K even if 1.5 GB
            async def _cursor_generator_4():
                yield doc_plain_1080p
            db.files.find = MagicMock(return_value=_cursor_generator_4())
            res = await db.find_cached_file("test-slug", "S1E01", "4K")
            self.assertIsNone(res)

        asyncio.run(_run_test())


if __name__ == "__main__":
    unittest.main()
