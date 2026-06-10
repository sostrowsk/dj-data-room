"""Tests for plan step W14-A8: watermark font via DATA_ROOM_WATERMARK_FONT.

``create_watermarks`` must resolve the font from the setting at call time:
explicit path => truetype font, None/unset => PIL default font. leasing keeps
today's Montserrat path pinned in settings.py.
"""

import os

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from data_room.utils import create_watermarks


class TestWatermarkFont(SimpleTestCase):
    def test_setting_is_honored_for_font_resolution(self):
        """A configured (broken) font path must reach the font loader —
        proves create_watermarks reads DATA_ROOM_WATERMARK_FONT."""
        with override_settings(DATA_ROOM_WATERMARK_FONT="/nonexistent/a8-font.ttf"):
            with self.assertRaises(OSError):
                create_watermarks("watermark text")

    @override_settings(DATA_ROOM_WATERMARK_FONT=None)
    def test_none_falls_back_to_pil_default_font(self):
        watermark1, watermark2, empty = create_watermarks("watermark text")
        self.assertGreater(watermark1.width, 0)
        self.assertGreater(watermark2.width, 0)
        self.assertGreater(empty.width, 0)

    def test_leasing_pins_montserrat_font(self):
        font_path = getattr(settings, "DATA_ROOM_WATERMARK_FONT", None)
        self.assertIsNotNone(font_path, "leasing must pin DATA_ROOM_WATERMARK_FONT")
        self.assertTrue(font_path.endswith("montserrat-v24-latin-500.ttf"))
        self.assertTrue(os.path.exists(font_path))
        watermark1, _, _ = create_watermarks("watermark text")
        self.assertGreater(watermark1.width, 0)
