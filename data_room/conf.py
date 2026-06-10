"""Host-configurable indirection for data_room's host-model coupling.

data_room links documents to a host "project" model and a host "client
company" model. Hosts repoint these via settings; the defaults match the
leasing monorepo:

- ``DATA_ROOM_PROJECT_MODEL`` (default ``project.Project``)
- ``DATA_ROOM_CLIENT_COMPANY_MODEL`` (default ``users.ClientCompany``)

Host URL indirection (Plan Phase 4A, step A9):

- ``DATA_ROOM_PROJECT_DETAIL_URL`` (default ``project:detail``) — URL *name*
  of the host's project detail page, reversible with ``kwargs={"pk": ...}``.
- Login redirects resolve ``settings.LOGIN_URL`` (name or path) instead of
  the hardcoded ``login`` URL name.

The resolver lookups are lazy (call-time), so data_room never imports the
host apps at module level. Note: the same settings are also read at
model-definition time for the FK strings in ``data_room.models`` — hosts
that override them need their own migrations (see plan risk 8 /
dj-data-room README).
"""

from django.apps import apps
from django.conf import settings
from django.shortcuts import resolve_url

DEFAULT_PROJECT_MODEL = "project.Project"
DEFAULT_CLIENT_COMPANY_MODEL = "users.ClientCompany"
DEFAULT_PROJECT_DETAIL_URL = "project:detail"


def get_project_model():
    """Return the host's project model class."""
    return apps.get_model(getattr(settings, "DATA_ROOM_PROJECT_MODEL", DEFAULT_PROJECT_MODEL))


def get_client_company_model():
    """Return the host's client-company model class."""
    return apps.get_model(getattr(settings, "DATA_ROOM_CLIENT_COMPANY_MODEL", DEFAULT_CLIENT_COMPANY_MODEL))


def get_project_detail_url() -> str:
    """URL name of the host's project detail page (``kwargs={"pk": ...}``)."""
    return getattr(settings, "DATA_ROOM_PROJECT_DETAIL_URL", DEFAULT_PROJECT_DETAIL_URL)


def get_login_url() -> str:
    """Login URL resolved from ``settings.LOGIN_URL`` (URL name or path)."""
    return resolve_url(settings.LOGIN_URL)
