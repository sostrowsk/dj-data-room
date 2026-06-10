# data_room/apps.py
from django.apps import AppConfig
from django.apps import apps as django_apps
from django.core import checks

#: Peer Django apps data_room imports at runtime (scribe.models.DocumentChunk
#: for chunk counting, progress.services for pipeline progress, ai_router for
#: get_llm_client/llm_log in the client matcher). Per architecture rule the
#: package does NOT declare them in pyproject — the host pins all dj-*
#: packages and this system check fails fast when a peer is missing.
PEER_APPS = ("scribe", "progress", "ai_router")


def check_peer_apps(app_configs, **kwargs):
    errors = []
    for index, peer in enumerate(PEER_APPS, start=1):
        if not django_apps.is_installed(peer):
            errors.append(
                checks.Error(
                    f"data_room requires the '{peer}' Django app to be installed.",
                    hint=f"Add '{peer}' to INSTALLED_APPS (host pins the dj-* package).",
                    id=f"data_room.E{index:03d}",
                )
            )
    return errors


class DataRoomConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "data_room"

    def ready(self):
        checks.register(check_peer_apps)
