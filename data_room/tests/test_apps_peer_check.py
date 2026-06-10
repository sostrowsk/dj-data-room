"""Peer-requirement system check: data_room requires scribe + progress + ai_router.

data_room imports ``scribe`` (chunk counting/indexing), ``progress``
(pipeline progress tracking) and ``ai_router`` (LLM client + llm_log in the
client matcher) at runtime but (per architecture rule) does NOT declare them
as package dependencies — the host pins all dj-* packages. The system check
makes a missing peer fail fast at ``manage.py check`` time.
"""

from unittest import mock

from django.test import SimpleTestCase


class TestDataRoomPeerCheck(SimpleTestCase):
    def test_check_passes_when_peers_installed(self):
        from data_room.apps import check_peer_apps

        self.assertEqual(check_peer_apps(app_configs=None), [])

    def test_check_reports_one_error_per_missing_peer(self):
        from data_room.apps import check_peer_apps

        with mock.patch("data_room.apps.django_apps.is_installed", return_value=False):
            errors = check_peer_apps(app_configs=None)

        self.assertEqual(
            {e.id for e in errors},
            {"data_room.E001", "data_room.E002", "data_room.E003"},
        )
        joined = " ".join(e.msg for e in errors)
        self.assertIn("scribe", joined)
        self.assertIn("progress", joined)
        self.assertIn("ai_router", joined)

    def test_check_is_registered_with_django(self):
        from django.core.checks.registry import registry

        from data_room.apps import check_peer_apps

        self.assertIn(check_peer_apps, registry.registered_checks)
