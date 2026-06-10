"""Tests for data_room.hooks (Plan Phase 4A, step A3).

data_room production code must not import the leasing host (ai_agents)
directly. Host tasks and providers are resolved lazily via dotted-path
settings in ``data_room/hooks.py``:

- ``DATA_ROOM_EXTRACTION_TASK`` (None => pipeline OCR fallback, admin noop)
- ``DATA_ROOM_CLIENT_EXTRACTION_TASK`` (None => manual trigger is a noop)
- ``DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER`` (unresolvable => frozen
  fallback codes, identical to today's registry output — choices drift
  would create phantom migrations)
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings

from data_room import hooks


def _dummy_hook():
    """Module-level dummy resolved via dotted path in override tests."""


class TestHookResolvers:
    def test_extraction_task_defaults_to_ai_agents_task(self):
        from ai_agents.tasks.extract_document_data import extract_document_data_task

        assert hooks.get_extraction_task() is extract_document_data_task

    def test_client_extraction_task_defaults_to_ai_agents_task(self):
        from ai_agents.tasks.extract_clients import extract_clients_for_document_task

        assert hooks.get_client_extraction_task() is extract_clients_for_document_task

    @override_settings(DATA_ROOM_EXTRACTION_TASK="data_room.tests.test_hooks._dummy_hook")
    def test_extraction_task_override_resolves_dotted_path(self):
        assert hooks.get_extraction_task() is _dummy_hook

    @override_settings(DATA_ROOM_EXTRACTION_TASK=None)
    def test_extraction_task_none_disables_feature(self):
        assert hooks.get_extraction_task() is None

    @override_settings(DATA_ROOM_CLIENT_EXTRACTION_TASK=None)
    def test_client_extraction_task_none_disables_feature(self):
        assert hooks.get_client_extraction_task() is None

    @override_settings(DATA_ROOM_EXTRACTION_TASK="nonexistent.module.task")
    def test_extraction_task_import_error_yields_none_and_warning(self, caplog):
        with caplog.at_level("WARNING", logger="data_room.hooks"):
            assert hooks.get_extraction_task() is None
        assert "DATA_ROOM_EXTRACTION_TASK" in caplog.text

    @override_settings(DATA_ROOM_CLIENT_EXTRACTION_TASK="nonexistent.module.task")
    def test_client_extraction_task_import_error_yields_none_and_warning(self, caplog):
        with caplog.at_level("WARNING", logger="data_room.hooks"):
            assert hooks.get_client_extraction_task() is None
        assert "DATA_ROOM_CLIENT_EXTRACTION_TASK" in caplog.text


class TestAccountingFrameworksProvider:
    def test_default_provider_is_registry(self):
        from ai_agents.services.frameworks.registry import get_framework_codes

        assert hooks.get_accounting_framework_codes() == tuple(get_framework_codes())

    @override_settings(DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER="nonexistent.module.provider")
    def test_unresolvable_provider_falls_back_to_frozen_codes(self, caplog):
        with caplog.at_level("WARNING", logger="data_room.hooks"):
            codes = hooks.get_accounting_framework_codes()
        assert codes == hooks.FALLBACK_ACCOUNTING_FRAMEWORK_CODES

    def test_frozen_fallback_matches_registry_output(self):
        """Drift guard: registry output and frozen fallback must stay identical,
        otherwise hosts without ai_agents get different model choices and
        ``makemigrations`` produces phantom migrations."""
        from ai_agents.services.frameworks.registry import get_framework_codes

        assert hooks.FALLBACK_ACCOUNTING_FRAMEWORK_CODES == tuple(get_framework_codes())

    def test_model_choices_use_hook_codes(self):
        from data_room.models import ProtectedClientDocument

        field = ProtectedClientDocument._meta.get_field("accounting_framework")
        assert [c[0] for c in field.choices] == list(hooks.get_accounting_framework_codes())


@pytest.fixture
def user(db):
    from users.factories import create_broker

    return create_broker()


@pytest.fixture
def queued_client_doc(db, user):
    from data_room.models import ProtectedClientDocument
    from users.models import ClientCompany

    cc = ClientCompany.objects.create(company="HookCo", register_number="HRB 4711", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=cc,
        name="d",
        user=user,
        user_type="broker",
        user_company="B",
        indexing_status="queued",
    )
    doc.file.save("f.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestPipelineWithoutExtractionTask:
    """DATA_ROOM_EXTRACTION_TASK=None => pipeline jumps to OCR markdown fallback."""

    @override_settings(DATA_ROOM_EXTRACTION_TASK=None)
    def test_pipeline_runs_ocr_fallback_then_indexing(self, queued_client_doc, user):
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task

        def fake_ocr(*args, **kwargs):
            ProtectedClientDocument.objects.filter(id=queued_client_doc.id).update(markdown="# md")
            return MagicMock()

        with patch(
            "ai_agents.tasks.extract_document_data.extract_document_data_task.apply"
        ) as mock_extract, patch(
            "data_room.tasks.index_document.extract_markdown_task.apply", side_effect=fake_ocr
        ) as mock_ocr, patch(
            "data_room.tasks.index_document.index_document_task.apply",
            return_value=MagicMock(result={"status": "completed"}),
        ) as mock_index:
            _process_single_document_task(queued_client_doc.id, user.id, "ProtectedClientDocument")

        mock_extract.assert_not_called()
        mock_ocr.assert_called_once()
        mock_index.assert_called_once()

    @override_settings(DATA_ROOM_EXTRACTION_TASK=None)
    def test_pipeline_marks_skipped_when_ocr_yields_no_markdown(self, queued_client_doc, user):
        from data_room.tasks.index_document import _process_single_document_task

        with patch(
            "ai_agents.tasks.extract_document_data.extract_document_data_task.apply"
        ) as mock_extract, patch("data_room.tasks.index_document.extract_markdown_task.apply") as mock_ocr, patch(
            "data_room.tasks.index_document.index_document_task.apply"
        ) as mock_index:
            _process_single_document_task(queued_client_doc.id, user.id, "ProtectedClientDocument")

        mock_extract.assert_not_called()
        mock_ocr.assert_called_once()
        mock_index.assert_not_called()
        queued_client_doc.refresh_from_db()
        assert queued_client_doc.indexing_status == "skipped"


@pytest.mark.django_db
class TestAdminReExtractNoop:
    """DATA_ROOM_EXTRACTION_TASK=None => admin re-extract is a noop."""

    @override_settings(DATA_ROOM_EXTRACTION_TASK=None)
    def test_re_extract_is_noop_without_extraction_task(self, queued_client_doc):
        from data_room.admin_actions import re_extract_and_remap_documents
        from data_room.models import ProtectedClientDocument

        ProtectedClientDocument.objects.filter(id=queued_client_doc.id).update(
            guv_mapping_status="ready", bilanz_mapping_status="ready"
        )

        with patch("ai_agents.tasks.extract_document_data.extract_document_data_task.delay") as mock_delay:
            count = re_extract_and_remap_documents([queued_client_doc.id])

        assert count == 0
        mock_delay.assert_not_called()
        queued_client_doc.refresh_from_db()
        assert queued_client_doc.guv_mapping_status == "ready"
        assert queued_client_doc.bilanz_mapping_status == "ready"


class TestRedisLockFactory:
    """A9: ``DATA_ROOM_REDIS_LOCK_FACTORY`` — None/unset => lockless."""

    def test_default_without_setting_is_none(self):
        from django.conf import settings
        from django.test import override_settings

        with override_settings():
            try:
                del settings.DATA_ROOM_REDIS_LOCK_FACTORY
            except AttributeError:
                pass
            assert hooks.get_redis_lock_factory() is None

    def test_leasing_setting_resolves_to_redis_client_factory(self):
        from leasing.redis_client import get_redis_client_from_env

        assert hooks.get_redis_lock_factory() is get_redis_client_from_env

    @override_settings(DATA_ROOM_REDIS_LOCK_FACTORY=None)
    def test_explicit_none_disables_locking(self):
        assert hooks.get_redis_lock_factory() is None

    @override_settings(DATA_ROOM_REDIS_LOCK_FACTORY="nonexistent.module.factory")
    def test_import_error_yields_none_and_warning(self, caplog):
        with caplog.at_level("WARNING", logger="data_room.hooks"):
            assert hooks.get_redis_lock_factory() is None
        assert "DATA_ROOM_REDIS_LOCK_FACTORY" in caplog.text


@pytest.mark.django_db
class TestLocklessIndexing:
    """Factory=None => indexing tasks run lockless, never touching the host
    redis client."""

    @override_settings(DATA_ROOM_REDIS_LOCK_FACTORY=None)
    def test_index_queried_documents_task_runs_lockless(self):
        from data_room.tasks.index_document import index_queried_documents_task

        with patch("leasing.redis_client.get_redis_client_from_env") as mock_factory:
            index_queried_documents_task.apply(args=[[]])

        mock_factory.assert_not_called()
