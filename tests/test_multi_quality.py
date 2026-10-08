import unittest
from bot import keyboards as kb


class TestMultiQuality(unittest.TestCase):
    def test_quality_code_map(self):
        self.assertEqual(kb.QUALITY_CODE_MAP["4"][0], "480p")
        self.assertEqual(kb.QUALITY_CODE_MAP["7"][0], "720p")
        self.assertEqual(kb.QUALITY_CODE_MAP["1"][0], "1080p")
        self.assertEqual(kb.QUALITY_CODE_MAP["k"][0], "4K")

    def test_quality_picker_all_button(self):
        markup = kb.quality_picker({"1080p", "720p", "480p"}, "lookism-s1e1", "rp:1", is_movie=False)
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any(cb.startswith("dl:all:") for cb in all_cbs))
        self.assertTrue(any(cb.startswith("mq_o:dl:") for cb in all_cbs))

    def test_batch_quality_picker_all_button(self):
        markup = kb.batch_quality_picker("lookism", 1)
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any(cb.startswith("bq:lookism:1:all") for cb in all_cbs))
        self.assertTrue(any(cb.startswith("mq_o:bq:") for cb in all_cbs))

    def test_multi_quality_picker_toggle(self):
        # Initial mask 471
        markup = kb.multi_quality_picker("dl", "lookism-s1e1", mask="471")
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        # Download selected button should submit 480p+720p+1080p
        self.assertTrue(any(cb == "dl:480p+720p+1080p:lookism-s1e1" for cb in all_cbs))
        # Toggling 4 should produce mask 71
        self.assertTrue(any("mq_t:dl:71:lookism-s1e1" in cb for cb in all_cbs))

    def test_multi_quality_picker_batch(self):
        markup = kb.multi_quality_picker("bq", "1:lookism", mask="47")
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertTrue(any(cb == "bq:lookism:1:480p+720p" for cb in all_cbs))

    def test_multi_quality_picker_empty(self):
        markup = kb.multi_quality_picker("dl", "lookism-s1e1", mask="")
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        self.assertIn("mq_empty", all_cbs)


if __name__ == "__main__":
    unittest.main()
