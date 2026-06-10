"""Tests for plan step W14-A8: slugify_de copy in data_room/utils.py.

data_room gets its own copy of ``slugify_de`` (pages stays untouched for the
other apps). Parity with the pages original is pinned so the copy cannot
silently drift while both still exist in the monorepo.
"""

from django.test import SimpleTestCase

from data_room.utils import slugify_de


class TestSlugifyDe(SimpleTestCase):
    def test_german_replacements(self):
        self.assertEqual(slugify_de("Jahresabschluß Müller & Söhne"), "jahresabschluss-mueller-soehne")
        self.assertEqual(slugify_de("Bilanz 2024.Q1/final"), "bilanz-2024-q1-final")
        self.assertEqual(slugify_de("GuV+Anhang ÄÖÜ"), "guvplusanhang-aeoeue")

    def test_parity_with_pages_original(self):
        from pages.utils import slugify_de as pages_slugify_de

        samples = [
            "Über-Maß.pdf",
            "Bericht 2025/2026 + Anlagen",
            "Größe & Gewicht (ÄÖÜß)",
            "plain-ascii_name 42",
        ]
        for sample in samples:
            self.assertEqual(slugify_de(sample), pages_slugify_de(sample), sample)
