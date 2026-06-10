"""P1.5 — Admin-Wiring der Re-Extract/Re-Map Actions.

Findings:
1. ProtectedClientDocumentAdmin.actions enthält die neuen Re-Map-Actions
   nicht (data_room/admin.py:178). Folge: Admin-User können sie nicht
   triggern.
2. re_extract_and_remap_project_documents filtert auf
   `ProtectedClientDocument.objects.filter(project__id__in=...)`, aber
   `ProtectedClientDocument` hat keine `project`-FK — nur `client`.
   Die korrekte Beziehung ist `client__projects__id__in` (related_name
   auf Project.client_company).
"""

from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import TestCase


@pytest.mark.django_db
class TestProtectedClientDocumentAdminWiring:
    """Admin-Action muss in ProtectedClientDocumentAdmin.actions stehen."""

    def test_actions_list_includes_re_extract_and_remap(self):
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        admin_instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        assert "re_extract_and_remap" in admin_instance.actions, (
            f"ProtectedClientDocumentAdmin.actions fehlt 're_extract_and_remap': " f"{admin_instance.actions}"
        )

    def test_per_doc_admin_action_dispatches_force_true_task(self):
        """Die Admin-Methode auf der ModelAdmin-Klasse muss
        re_extract_and_remap_documents([doc.id, ...]) rufen."""
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.core.files.base import ContentFile
        from django.test import RequestFactory

        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument
        from users.factories import create_broker
        from users.models import ClientCompany

        broker = create_broker()
        client = ClientCompany.objects.create(company="P1.5-Co", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=client,
            name="d1",
            user=broker,
            user_type="broker",
            user_company="x",
            indexing_status="pending",
            guv_mapping_status="ready",
            bilanz_mapping_status="ready",
        )
        doc.file.save("d1.pdf", ContentFile(b"%PDF-1.4 fake"))
        admin_instance = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        qs = ProtectedClientDocument.objects.filter(id=doc.id)
        request = RequestFactory().post("/")
        request.user = broker
        # FallbackStorage für messages.info / messages.success.
        setattr(request, "session", {})
        setattr(request, "_messages", FallbackStorage(request))
        with patch(
            "ai_agents.tasks.extract_document_data.extract_document_data_task.delay"
        ) as mock_delay, TestCase.captureOnCommitCallbacks(execute=True):
            admin_instance.re_extract_and_remap(request, qs)
        doc.refresh_from_db()
        assert doc.guv_mapping_status == "re_mapping"
        assert doc.bilanz_mapping_status == "re_mapping"
        mock_delay.assert_called_once()
        assert mock_delay.call_args.kwargs.get("force") is True


@pytest.mark.django_db
class TestProjectBulkActionResolvesDocsViaClient:
    """re_extract_and_remap_project_documents muss Docs via
    `client__projects__id__in` finden — nicht über non-existente
    `project`-FK (würde FieldError werfen)."""

    def test_project_bulk_resolves_via_client_projects_relation(self):
        from data_room.admin_actions import re_extract_and_remap_project_documents
        from data_room.models import ProtectedClientDocument
        from project.models import Project
        from users.factories import create_broker
        from users.models import ClientCompany
        from users.tests.factories import BrokerCompanyFactory

        broker = create_broker()
        broker_company = BrokerCompanyFactory()
        client = ClientCompany.objects.create(company="P1.5-ClientCo", is_active=True)
        project = Project.objects.create(
            client_company=client,
            broker_company=broker_company,
        )
        doc = ProtectedClientDocument.objects.create(
            client=client,
            name="bulk-doc",
            user=broker,
            user_type="broker",
            user_company="x",
            indexing_status="pending",
            guv_mapping_status="ready",
            bilanz_mapping_status="ready",
        )

        with patch(
            "ai_agents.tasks.extract_document_data.extract_document_data_task.delay"
        ) as mock_delay, TestCase.captureOnCommitCallbacks(execute=True):
            count = re_extract_and_remap_project_documents([project.id])

        assert count == 1, "Bulk-Action muss den Doc via client__projects-Relation finden"
        doc.refresh_from_db()
        assert doc.guv_mapping_status == "re_mapping"
        mock_delay.assert_called_once()


@pytest.mark.django_db
class TestProjectBulkCoversActiveGroup:
    """Plan-P2.B: project bulk muss alle aktiven Gruppe-ClientCompanies
    decken (holding/subsidiary), nicht nur project.client_company.
    Memo-Scope läuft über get_active_company_ids — die Bulk-Action
    soll das gleiche Set abdecken."""

    def test_subsidiary_doc_remapped_via_project_bulk(self):
        from data_room.admin_actions import re_extract_and_remap_project_documents
        from data_room.models import ProtectedClientDocument
        from project.models import Project
        from users.factories import create_broker
        from users.models import ClientCompany
        from users.tests.factories import BrokerCompanyFactory

        broker = create_broker()
        broker_company = BrokerCompanyFactory()
        # Holding (primary), Tochter (subsidiary) — beide in Gruppe.
        holding = ClientCompany.objects.create(company="P2.B-Holding", is_active=True)
        subsidiary = ClientCompany.objects.create(
            company="P2.B-Sub",
            is_active=True,
            holding=holding,
        )
        project = Project.objects.create(
            client_company=holding,
            broker_company=broker_company,
        )
        # Subsidiary-Doc — von der primären FK NICHT erreichbar.
        sub_doc = ProtectedClientDocument.objects.create(
            client=subsidiary,
            name="sub-doc",
            user=broker,
            user_type="broker",
            user_company="x",
            indexing_status="pending",
            guv_mapping_status="ready",
            bilanz_mapping_status="ready",
        )

        with patch(
            "ai_agents.tasks.extract_document_data.extract_document_data_task.delay"
        ) as mock_delay, TestCase.captureOnCommitCallbacks(execute=True):
            count = re_extract_and_remap_project_documents([project.id])

        # Subsidiary-Doc muss im Bulk landen — Group-Scope.
        assert count >= 1, "P2.B: project bulk muss subsidiary-Doc finden"
        sub_doc.refresh_from_db()
        assert sub_doc.guv_mapping_status == "re_mapping"
        mock_delay.assert_called()


@pytest.mark.django_db
class TestProjectAdminBulkAction:
    """ProjectAdmin.actions muss re_extract_and_remap_project_documents
    haben, damit Admin-User das Bulk-Re-Run aus der Project-Liste
    triggern können."""

    def test_project_admin_actions_include_re_extract(self):
        from project.admin import ProjectAdmin
        from project.models import Project

        admin_instance = ProjectAdmin(Project, AdminSite())
        assert "re_extract_and_remap_project_documents" in admin_instance.actions, (
            f"ProjectAdmin.actions fehlt 're_extract_and_remap_project_documents': " f"{admin_instance.actions}"
        )
