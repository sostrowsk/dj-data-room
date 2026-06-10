# data_room/policies.py
"""Pluggable permission-policy framework for the data_room package (plan A4).

The active policy is resolved from the ``DATA_ROOM_PERMISSION_POLICY``
setting (dotted path to a :class:`BaseDataRoomPolicy` subclass, default
:class:`DefaultPolicy`) and cached for the process lifetime — hosts set it
once at startup. Tests that need to swap policies call
``get_policy.cache_clear()`` around ``override_settings``.

All methods are duck-typed against host models: ``user`` is the request
user, ``document`` a project/client document, ``project`` / ``company``
host model instances. The policy never imports host apps.
"""

import functools

from django.conf import settings
from django.utils.module_loading import import_string

DEFAULT_POLICY_PATH = "data_room.policies.DefaultPolicy"


class BaseDataRoomPolicy:
    """Interface contract for data_room permission policies.

    Method list mirrors the real gate callsites in the package (read
    gates today; write/manage/curate/bucket gates are wired in plan
    steps A5/A6). Subclasses must implement every method.
    """

    def can_view_project_document(self, user, document):
        raise NotImplementedError

    def can_view_client_document(self, user, document):
        raise NotImplementedError

    def can_access_project(self, user, project):
        raise NotImplementedError

    def can_manage_project_document(self, user, document):
        raise NotImplementedError

    def can_manage_project_documents(self, user, project):
        raise NotImplementedError

    def can_manage_client_document(self, user, document):
        raise NotImplementedError

    def can_download_original(self, user, document):
        raise NotImplementedError

    def can_curate_clients(self, user, document=None):
        """May ``user`` confirm/edit the clients linked to documents?
        ``document`` is optional — the gate runs before the lookup in the
        curate views, which therefore call it with ``document=None``."""
        raise NotImplementedError

    def filter_project_document_buckets(self, user, project, buckets, restrict_drafts=True):
        """Return ``buckets`` (mapping of author role -> document queryset)
        narrowed to what ``user`` may see on ``project``.

        ``restrict_drafts=False`` keeps unreviewed/disabled documents in
        the buckets (used by the management document list); author-scope
        narrowing still applies."""
        raise NotImplementedError

    def can_view_company_documents(self, user, company):
        raise NotImplementedError

    def get_author_scope(self, user, documents):
        """Return the ``documents`` queryset narrowed to the author scope
        of ``user`` (e.g. own/own-company authored documents)."""
        raise NotImplementedError


class DefaultPolicy(BaseDataRoomPolicy):
    """Conservative generic policy: staff/superuser may do everything,
    the document author may view and manage their own documents, project
    access delegates to the host's ``project.check_permissions(user)``
    when available — everything else is denied.
    """

    @staticmethod
    def _is_staff(user):
        return bool(getattr(user, "is_authenticated", False) and (user.is_superuser or user.is_staff))

    @staticmethod
    def _is_author(user, document):
        if not getattr(user, "is_authenticated", False):
            return False
        return document.user_id is not None and document.user_id == user.pk

    def _staff_or_author(self, user, document):
        return self._is_staff(user) or self._is_author(user, document)

    def can_view_project_document(self, user, document):
        return self._staff_or_author(user, document)

    def can_view_client_document(self, user, document):
        return self._staff_or_author(user, document)

    def can_access_project(self, user, project):
        check_permissions = getattr(project, "check_permissions", None)
        if callable(check_permissions):
            return check_permissions(user)
        return self._is_staff(user)

    def can_manage_project_document(self, user, document):
        return self._staff_or_author(user, document)

    def can_manage_project_documents(self, user, project):
        return self._is_staff(user)

    def can_manage_client_document(self, user, document):
        return self._staff_or_author(user, document)

    def can_download_original(self, user, document):
        return self._staff_or_author(user, document)

    def can_curate_clients(self, user, document=None):
        if document is None:
            return self._is_staff(user)
        return self._staff_or_author(user, document)

    def filter_project_document_buckets(self, user, project, buckets, restrict_drafts=True):
        return buckets

    def can_view_company_documents(self, user, company):
        return self._is_staff(user)

    def get_author_scope(self, user, documents):
        if self._is_staff(user):
            return documents
        return documents.filter(user=user)


@functools.lru_cache(maxsize=None)
def get_policy():
    """Return the cached policy instance configured via
    ``DATA_ROOM_PERMISSION_POLICY`` (default :class:`DefaultPolicy`)."""
    policy_path = getattr(settings, "DATA_ROOM_PERMISSION_POLICY", DEFAULT_POLICY_PATH)
    return import_string(policy_path)()
