"""Host hooks for data_room (Plan Phase 4A, step A3).

data_room must not import the leasing host (``ai_agents``) directly.
Host-provided callables are resolved lazily — at call time, never at
import time — via dotted-path settings:

``DATA_ROOM_EXTRACTION_TASK``
    Celery task for LLM document extraction. Contract:
    ``.apply(args=[doc_id], kwargs={"user_id", "document_model",
    "pipeline_progress_id", "progress_range"})`` (pipeline) and
    ``.delay(doc_id, force=True)`` (admin re-extract).
    ``None`` => pipeline jumps to the OCR markdown fallback; the admin
    re-extract action is a noop.

``DATA_ROOM_CLIENT_EXTRACTION_TASK``
    Celery task for client extraction. Contract:
    ``.delay(document_id=..., user_id=..., document_model=...)``.
    ``None`` => the manual trigger view is a noop.

``DATA_ROOM_REDIS_LOCK_FACTORY``
    Factory returning a redis client whose ``get_lock(...)`` provides the
    distributed lock used by the indexing tasks/commands. Default ``None``
    => lockless operation (no host redis dependency).

``DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER``
    Callable returning the accounting framework codes used for the
    ``ProtectedClientDocument.accounting_framework`` choices.
    Unresolvable => ``FALLBACK_ACCOUNTING_FRAMEWORK_CODES`` (frozen copy
    of today's registry output — choices drift would create phantom
    migrations in hosts without the provider).

Defaults point at the leasing host. An unresolvable dotted path logs a
warning and disables the feature (returns ``None``) instead of crashing.
"""

import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_TASK = "ai_agents.tasks.extract_document_data.extract_document_data_task"
DEFAULT_CLIENT_EXTRACTION_TASK = "ai_agents.tasks.extract_clients.extract_clients_for_document_task"
DEFAULT_ACCOUNTING_FRAMEWORKS_PROVIDER = "ai_agents.services.frameworks.registry.get_framework_codes"

# Frozen copy of ai_agents...registry.get_framework_codes() output. Used when
# the provider is unresolvable so model choices (and thus migrations) stay
# byte-stable. Guarded against drift by test_frozen_fallback_matches_registry_output.
FALLBACK_ACCOUNTING_FRAMEWORK_CODES = (
    "AT_UGB_GKV",
    "AT_UGB_UKV",
    "CZ_GAAP_FUNCTION",
    "CZ_GAAP_NATURE",
    "DE_HGB_GKV",
    "DE_HGB_UKV",
    "DK_GAAP_FUNCTION",
    "DK_GAAP_NATURE",
    "IFRS_FUNCTION",
    "IFRS_NATURE",
    "OTHER",
)


def _resolve(setting_name: str, default_path: str):
    """Resolve a dotted-path setting to a callable.

    Explicit ``None`` disables the feature silently; an unresolvable path
    logs a warning and disables the feature (returns ``None``).
    """
    path = getattr(settings, setting_name, default_path)
    if not path:
        return None
    try:
        return import_string(path)
    except ImportError:
        logger.warning("%s points to unresolvable %r — feature disabled.", setting_name, path)
        return None


def get_extraction_task():
    """Document extraction Celery task or ``None`` (feature off)."""
    return _resolve("DATA_ROOM_EXTRACTION_TASK", DEFAULT_EXTRACTION_TASK)


def get_client_extraction_task():
    """Client extraction Celery task or ``None`` (feature off)."""
    return _resolve("DATA_ROOM_CLIENT_EXTRACTION_TASK", DEFAULT_CLIENT_EXTRACTION_TASK)


def get_redis_lock_factory():
    """Redis client factory for distributed locks or ``None`` (lockless)."""
    return _resolve("DATA_ROOM_REDIS_LOCK_FACTORY", None)


def get_accounting_framework_codes() -> tuple:
    """Accounting framework codes for model choices.

    Falls back to the frozen code list when the provider is unresolvable,
    keeping migrations byte-stable in hosts without the default provider.
    """
    provider = _resolve("DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER", DEFAULT_ACCOUNTING_FRAMEWORKS_PROVIDER)
    if provider is None:
        return FALLBACK_ACCOUNTING_FRAMEWORK_CODES
    return tuple(provider())
