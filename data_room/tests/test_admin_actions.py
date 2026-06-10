"""Tests for admin actions on ProtectedClientDocument and ProtectedProjectDocument.

Covers the new `rerun_full_pipeline` action and ClientDoc `restart_indexing`.
"""

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.files.base import ContentFile

from data_room.admin import ProtectedClientDocumentAdmin, ProtectedProjectDocumentAdmin
from data_room.models import ProtectedClientDocument, ProtectedProjectDocument
from users.factories import create_broker
from users.models import ClientCompany


@pytest.fixture
def admin_request(rf):
    """Request object for admin actions (uses broker as user)."""
    user = create_broker()
    request = rf.post("/admin/")
    request.user = user
    # Django admin messages framework needs _messages attribute
    from django.contrib.messages.storage.fallback import FallbackStorage

    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))
    return request


@pytest.fixture
def client_company(db):
    return ClientCompany.objects.create(company="TestCo GmbH", register_number="HRB 1", is_active=True)


@pytest.fixture
def client_doc(db, client_company, admin_request):
    doc = ProtectedClientDocument.objects.create(
        client=client_company,
        name="test-doc",
        user=admin_request.user,
        user_type="broker",
        user_company="Broker Ltd",
        indexing_status="indexed",
        client_extraction_status="completed",
        financial_extraction_status="completed",
        guv_json={"raw_positions": []},
        bilanz_json={"raw_positions": []},
        company_info_json={"firma": "TestCo"},
        markdown="existing markdown",
        tokens=100,
        guv_extracted=True,
        bilanz_extracted=True,
    )
    doc.file.save("test.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.fixture
def project_doc(db, admin_request):
    from project.tests.factories import ProjectFactory

    project = ProjectFactory()
    doc = ProtectedProjectDocument.objects.create(
        project=project,
        name="proj-doc",
        user=admin_request.user,
        user_type="broker",
        user_company="Broker Ltd",
        indexing_status="indexed",
        extraction_status="completed",
        financing_object_json={"objekt_bezeichnung": "X"},
        risk_factors_json={"strengths": []},
        markdown="existing md",
        tokens=50,
    )
    doc.file.save("proj.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestClientDocRerunFullPipeline:
    @patch("data_room.admin.process_document_pipeline_task")
    def test_resets_all_fields_and_triggers_pipeline(self, mock_task, admin_request, client_doc):
        admin_instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        queryset = ProtectedClientDocument.objects.filter(id=client_doc.id)

        admin_instance.rerun_full_pipeline(admin_request, queryset)

        client_doc.refresh_from_db()
        assert client_doc.indexing_status == "pending"
        assert client_doc.client_extraction_status == "pending"
        assert client_doc.financial_extraction_status == "pending"
        assert client_doc.guv_json is None
        assert client_doc.bilanz_json is None
        assert client_doc.company_info_json is None
        assert client_doc.extracted_clients_data == []
        assert client_doc.guv_extracted is False
        assert client_doc.bilanz_extracted is False
        assert client_doc.markdown == ""
        assert client_doc.tokens == 0

        mock_task.delay.assert_called_once_with([client_doc.id], admin_request.user.id, "ProtectedClientDocument")

    @patch("data_room.admin.process_document_pipeline_task")
    def test_empty_queryset_does_not_trigger_task(self, mock_task, admin_request):
        admin_instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        queryset = ProtectedClientDocument.objects.none()

        admin_instance.rerun_full_pipeline(admin_request, queryset)

        mock_task.delay.assert_not_called()


@pytest.mark.django_db
class TestProjectDocRerunFullPipeline:
    @patch("data_room.admin.process_document_pipeline_task")
    def test_resets_all_fields_and_triggers_pipeline(self, mock_task, admin_request, project_doc):
        admin_instance = ProtectedProjectDocumentAdmin(ProtectedProjectDocument, AdminSite())
        queryset = ProtectedProjectDocument.objects.filter(id=project_doc.id)

        admin_instance.rerun_full_pipeline(admin_request, queryset)

        project_doc.refresh_from_db()
        assert project_doc.indexing_status == "pending"
        assert project_doc.extraction_status == "pending"
        assert project_doc.financing_object_json is None
        assert project_doc.risk_factors_json is None
        assert project_doc.markdown == ""
        assert project_doc.tokens == 0

        mock_task.delay.assert_called_once_with([project_doc.id], admin_request.user.id, "ProtectedProjectDocument")


@pytest.mark.django_db
class TestClientDocRestartIndexing:
    @patch("data_room.admin.process_document_pipeline_task")
    def test_restart_indexing_only_resets_indexing_status(self, mock_task, admin_request, client_doc):
        """restart_indexing keeps extraction data, only re-does Milvus indexing."""
        admin_instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        queryset = ProtectedClientDocument.objects.filter(id=client_doc.id)

        admin_instance.restart_indexing(admin_request, queryset)

        client_doc.refresh_from_db()
        assert client_doc.indexing_status == "pending"
        # Extraction data must remain untouched
        assert client_doc.client_extraction_status == "completed"
        assert client_doc.financial_extraction_status == "completed"
        assert client_doc.guv_json == {"raw_positions": []}
        assert client_doc.bilanz_json == {"raw_positions": []}
        assert client_doc.markdown == "existing markdown"

        mock_task.delay.assert_called_once_with([client_doc.id], admin_request.user.id, "ProtectedClientDocument")
