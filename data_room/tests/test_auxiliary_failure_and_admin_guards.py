"""Regression tests for codex review round 5 findings.

P1: When extract_document_data_task returns `failed`, the pipeline aborts.
    But a `failed` return can come from a non-critical auxiliary call
    (e.g. GuV/Bilanz/RiskFactors transient error) while markdown was
    extracted successfully. Indexing should still proceed if markdown exists.
P2: Admin requeue actions (rerun_full_pipeline, restart_indexing) must
    exclude documents that are already in active pipeline states
    (processing/chunking/indexing) to prevent duplicate workers racing.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.files.base import ContentFile


@pytest.fixture
def user(db):
    from users.factories import create_broker

    return create_broker()


@pytest.fixture
def admin_request(rf, user):
    request = rf.post("/admin/")
    request.user = user
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))
    return request


@pytest.fixture
def active_client_doc(db, user):
    """Client doc currently in active pipeline state (chunking)."""
    from data_room.models import ProtectedClientDocument
    from users.models import ClientCompany

    cc = ClientCompany.objects.create(company="TestCoActive", register_number="HRB 99", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=cc,
        name="active-doc",
        user=user,
        user_type="broker",
        user_company="B",
        indexing_status="chunking",  # another worker is mid-pipeline
    )
    doc.file.save("active.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestIndexingProceedsDespiteAuxFailure:
    """P1: If markdown exists, indexing must run even if extraction reported failed."""

    def test_indexing_runs_when_markdown_extracted_but_aux_call_failed(self, user):
        """Markdown is the critical path; auxiliary call failures should not block indexing."""
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task
        from users.models import ClientCompany

        cc = ClientCompany.objects.create(company="TC", register_number="HRB 88", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=cc,
            name="d",
            user=user,
            user_type="broker",
            user_company="B",
            indexing_status="pending",
            # markdown populated (simulating a partial success)
            markdown="# some markdown",
        )
        doc.file.save("f.pdf", ContentFile(b"%PDF-1.4 fake"))

        aux_fail = {"status": "failed", "error": "GuV call timed out"}
        with patch("ai_agents.tasks.extract_document_data.extract_document_data_task.apply") as mock_extract, patch(
            "data_room.tasks.index_document.index_document_task.apply"
        ) as mock_index:
            mock_extract.return_value = MagicMock(result=aux_fail)
            mock_index.return_value = MagicMock(result={"status": "success"})

            _process_single_document_task(doc.id, user.id, "ProtectedClientDocument")

            # Indexing must still run — markdown is available
            mock_index.assert_called_once()


@pytest.mark.django_db
class TestAdminRequeueSkipsActiveDocs:
    """P2: Admin actions must not overwrite status of docs already mid-pipeline."""

    def test_rerun_full_pipeline_skips_active_client_doc(self, active_client_doc, admin_request):
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        qs = ProtectedClientDocument.objects.filter(id=active_client_doc.id)

        with patch("data_room.admin.process_document_pipeline_task") as mock_task:
            instance.rerun_full_pipeline(admin_request, qs)

            mock_task.delay.assert_not_called()

        # Status must remain active, not overwritten to pending
        active_client_doc.refresh_from_db()
        assert active_client_doc.indexing_status == "chunking"

    def test_restart_indexing_skips_active_client_doc(self, active_client_doc, admin_request):
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        qs = ProtectedClientDocument.objects.filter(id=active_client_doc.id)

        with patch("data_room.admin.process_document_pipeline_task") as mock_task:
            instance.restart_indexing(admin_request, qs)

            mock_task.delay.assert_not_called()

        active_client_doc.refresh_from_db()
        assert active_client_doc.indexing_status == "chunking"
