"""Regression tests: read access to client documents for non-broker user types.

Covers the 403 bug where partner/client users could not download
``/client-doc/pdf/<pk>/`` or ``/client-doc/original/<pk>/`` even when they
were legitimately associated with the document's client company via a
project (partner) or direct ownership (client).
"""

from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from data_room.models import ProtectedClientDocument
from project.tests.project_utils import create_project
from users.factories import (
    create_admin,
    create_broker,
    create_client,
    create_partner,
    login_and_verify,
)


def _make_client_document(*, client_company, author, user_type="broker"):
    doc = ProtectedClientDocument.objects.create(
        client=client_company,
        name="doc",
        user=author,
        user_type=user_type,
        user_company=getattr(client_company, "company", "C"),
        reviewed=True,
        disabled=False,
    )
    doc.file.save("doc.pdf", ContentFile(b"%PDF-1.4 fake"))
    doc.original.save("doc_original.pdf", ContentFile(b"%PDF-1.4 fake original"))
    return doc


@pytest.fixture
def doc_on_shared_project(db):
    broker = create_broker()
    partner = create_partner()
    client_user = create_client(broker=broker)
    client_company = client_user.client_company
    project = create_project(
        broker_company=broker.broker_company,
        client_company=client_company,
        leasing_company=partner.leasing_company,
        user=client_user,
    )
    project.invited_leasing_companies.add(partner.leasing_company)
    doc = _make_client_document(client_company=client_company, author=broker)
    return {
        "doc": doc,
        "broker": broker,
        "partner": partner,
        "client_user": client_user,
        "client_company": client_company,
        "project": project,
    }


def _pdf_url(doc):
    return reverse("data_room:client-doc-pdf", kwargs={"pk": doc.pk})


def _original_url(doc):
    return reverse("data_room:client-doc-original", kwargs={"pk": doc.pk})


def _mock_pdf():
    return patch(
        "data_room.views.api.protected_document.DocumentProcessor.generate_pdf",
        return_value=b"%PDF-1.4 rendered",
    )


@pytest.mark.django_db
class TestClientAllProjectsInactive:
    """If doc.client is explicitly inactive on EVERY project it belongs to, even
    direct-lineage clients must not be able to fetch it via URL."""

    def test_client_blocked_when_doc_client_inactive_on_every_project(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink

        ctx = doc_on_shared_project
        # The doc's client is on exactly one project (ctx["project"]). Mark it inactive.
        ProjectCompanyLink.objects.create(
            project=ctx["project"],
            client=ctx["client_company"],
            is_active=False,
        )
        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestProjectZipAppliesCheckPermissions:
    """project_zip_task must run every client document through check_permissions,
    not a raw QuerySet on active_company_ids — otherwise partners/clients get
    denied docs via the ZIP download even when direct URL access is blocked."""

    def test_zip_task_excludes_client_docs_denied_by_check_permissions(self, doc_on_shared_project):
        from unittest.mock import patch as mpatch

        from data_room.tasks.project_zip import project_zip_task

        ctx = doc_on_shared_project
        # An unrelated broker triggers the ZIP; their broker_company does NOT match
        # the project, so doc must be filtered out.
        outsider = create_broker()

        # Patch the symbol where it's bound (module-level import in the task),
        # not the origin module — otherwise the real ProgressTrackerService runs.
        with mpatch("zipfile.ZipFile") as mock_zipfile, mpatch("builtins.open", create=True), mpatch(
            "data_room.tasks.project_zip.ProgressTrackerService"
        ):
            mock_zip_instance = mock_zipfile.return_value.__enter__.return_value
            result = project_zip_task(ctx["project"].id, outsider.id)

        assert result.get("status") == "failed"
        assert "No accessible documents" in result.get("error", "")
        # The task uses writestr(), not write(), so assert against the right call list.
        written_paths = [call.args[0] for call in mock_zip_instance.writestr.call_args_list if call.args]
        assert not any(ctx["doc"].name in p for p in written_paths), f"forbidden doc landed in ZIP: {written_paths}"

    def test_zip_includes_broker_triage_drafts(self, doc_on_shared_project):
        """Finding #1: check_permissions() lets in-scope brokers see unreviewed
        drafts for triage. The ZIP must not silently drop them."""
        from unittest.mock import patch as mpatch

        from data_room.tasks.project_zip import project_zip_task

        ctx = doc_on_shared_project
        # Flip the fixture doc to an unreviewed draft.
        ctx["doc"].reviewed = False
        ctx["doc"].save(update_fields=["reviewed"])

        # The project's own broker triggers the ZIP; they are in scope for
        # check_permissions() and see the draft via direct URL. Must also see
        # it in the ZIP.
        broker = ctx["broker"]

        with mpatch("zipfile.ZipFile") as mock_zipfile, mpatch(
            "data_room.views.api.protected_document.DocumentProcessor.generate_pdf",
            return_value=b"%PDF-1.4 rendered",
        ), mpatch("data_room.tasks.project_zip.ProgressTrackerService"):
            mock_zip_instance = mock_zipfile.return_value.__enter__.return_value
            result = project_zip_task(ctx["project"].id, broker.id)

        assert result.get("status") == "success", f"got: {result}"
        written_paths = [call.args[0] for call in mock_zip_instance.writestr.call_args_list if call.args]
        assert any(
            ctx["doc"].name.split(".")[0] in p for p in written_paths
        ), f"broker triage draft missing from ZIP: {written_paths}"


@pytest.mark.django_db
class TestModelCheckPermissionsScope:
    """ProtectedClientDocument.check_permissions is the SSOT for read scoping —
    it must itself implement the project-scoped rules, not just the view wrapper."""

    def _verified(self, user):
        # Model uses user.is_verified(); bypass OTP for unit-level model assertions.
        user.is_verified = lambda: True
        return user

    def test_unrelated_broker_denied_on_model(self, doc_on_shared_project):
        ctx = doc_on_shared_project
        outsider = self._verified(create_broker())
        assert ctx["doc"].check_permissions(outsider) is False

    def test_project_broker_allowed_on_model(self, doc_on_shared_project):
        ctx = doc_on_shared_project
        broker = self._verified(ctx["broker"])
        assert ctx["doc"].check_permissions(broker) is True

    def test_unrelated_partner_denied_on_model(self, doc_on_shared_project):
        ctx = doc_on_shared_project
        outsider = self._verified(create_partner())
        assert ctx["doc"].check_permissions(outsider) is False

    def test_invited_partner_allowed_on_model(self, doc_on_shared_project):
        ctx = doc_on_shared_project
        partner = self._verified(ctx["partner"])
        assert ctx["doc"].check_permissions(partner) is True


@pytest.mark.django_db
class TestClientDocumentPdfRead:
    def test_partner_invited_on_project_can_download_pdf(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_client_of_same_client_company_can_download_pdf(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_broker_of_project_broker_company_can_download_pdf(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_admin_can_download_pdf(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(create_admin(), client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_unrelated_partner_gets_403_on_pdf(self, doc_on_shared_project, client):
        outsider = create_partner()
        login_and_verify(outsider, client)
        with _mock_pdf():
            response = client.get(_pdf_url(doc_on_shared_project["doc"]))
        assert response.status_code == 403

    def test_unrelated_client_user_gets_403_on_pdf(self, doc_on_shared_project, client):
        outsider = create_client()
        login_and_verify(outsider, client)
        with _mock_pdf():
            response = client.get(_pdf_url(doc_on_shared_project["doc"]))
        assert response.status_code == 403

    def test_unrelated_broker_gets_403_on_pdf(self, doc_on_shared_project, client):
        outsider = create_broker()
        login_and_verify(outsider, client)
        with _mock_pdf():
            response = client.get(_pdf_url(doc_on_shared_project["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentPdfReviewedGate:
    """Non-admins must not see unreviewed/disabled broker-authored drafts."""

    def test_partner_cannot_download_unreviewed_broker_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].reviewed = False
        ctx["doc"].save(update_fields=["reviewed"])
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403

    def test_partner_cannot_download_disabled_broker_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].disabled = True
        ctx["doc"].save(update_fields=["disabled"])
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403

    def test_client_cannot_download_disabled_broker_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].disabled = True
        ctx["doc"].save(update_fields=["disabled"])
        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentBrokerRelinking:
    """Broker keeps access when a doc is relinked to a group subsidiary without its own project."""

    def test_broker_can_download_doc_relinked_to_subsidiary(self, doc_on_shared_project, client):
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        subsidiary = ClientCompany.objects.create(
            company="Subsidiary GmbH",
            broker_company=ctx["broker"].broker_company,
        )
        ctx["doc"].client = subsidiary
        ctx["doc"].save(update_fields=["client"])

        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentPartnerScoping:
    """Partner access must honor PartnerStatus.excluded and partner-authored scoping."""

    def test_excluded_partner_gets_403_even_if_invited(self, doc_on_shared_project, client):
        from project.models import PartnerStatus

        ctx = doc_on_shared_project
        PartnerStatus.objects.create(
            project=ctx["project"],
            partner=ctx["partner"].leasing_company,
            excluded=True,
        )
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403

    def test_partner_authored_doc_not_visible_to_other_invited_partners(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        other_partner = create_partner()
        ctx["project"].invited_leasing_companies.add(other_partner.leasing_company)
        ctx["project"].leasing_companies.add(other_partner.leasing_company)

        # Relabel the doc as partner-authored by ctx["partner"].
        ctx["doc"].user = ctx["partner"]
        ctx["doc"].user_type = "partner"
        ctx["doc"].save(update_fields=["user", "user_type"])

        login_and_verify(other_partner, client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403

    def test_partner_authored_doc_visible_to_its_own_leasing_company(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].user = ctx["partner"]
        ctx["doc"].user_type = "partner"
        ctx["doc"].save(update_fields=["user", "user_type"])

        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentClientHoldingHierarchy:
    """Client users can read docs of their holding ancestors / descendants."""

    def test_client_of_parent_can_download_subsidiary_doc(self, doc_on_shared_project, client):
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        subsidiary = ClientCompany.objects.create(
            company="Subsidiary GmbH",
            broker_company=ctx["broker"].broker_company,
            holding=ctx["client_company"],
        )
        ctx["doc"].client = subsidiary
        ctx["doc"].save(update_fields=["client"])

        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_client_of_subsidiary_can_download_parent_doc(self, doc_on_shared_project, client):
        from users.factories import create_client as make_client
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        parent = ClientCompany.objects.create(
            company="Holding AG",
            broker_company=ctx["broker"].broker_company,
        )
        ctx["client_company"].holding = parent
        ctx["client_company"].save(update_fields=["holding"])

        ctx["doc"].client = parent
        ctx["doc"].save(update_fields=["client"])

        # ctx["client_user"] belongs to the subsidiary (ctx["client_company"]).
        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200
        # and an unrelated client from a totally different group still 403
        outsider = make_client()
        login_and_verify(outsider, client)
        with _mock_pdf():
            response2 = client.get(_pdf_url(ctx["doc"]))
        assert response2.status_code == 403


@pytest.mark.django_db
class TestClientDocumentOriginalIsPrivileged:
    """Raw (unwatermarked) originals stay broker/admin/staff-only."""

    def test_partner_cannot_download_original_even_if_can_read_pdf(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["partner"], client)
        response = client.get(_original_url(ctx["doc"]))
        assert response.status_code == 403

    def test_client_cannot_download_original_of_own_company_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["client_user"], client)
        response = client.get(_original_url(ctx["doc"]))
        assert response.status_code == 403

    def test_broker_still_downloads_original(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["broker"], client)
        response = client.get(_original_url(ctx["doc"]))
        assert response.status_code == 200

    def test_unrelated_broker_blocked_from_original(self, doc_on_shared_project, client):
        outsider = create_broker()
        login_and_verify(outsider, client)
        response = client.get(_original_url(doc_on_shared_project["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentBrokerTriageDrafts:
    """Broker must still download unreviewed/disabled client-authored drafts for triage."""

    def test_broker_downloads_unreviewed_client_authored_draft(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].user = ctx["client_user"]
        ctx["doc"].user_type = "client"
        ctx["doc"].reviewed = False
        ctx["doc"].save(update_fields=["user", "user_type", "reviewed"])
        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_broker_downloads_disabled_partner_authored_draft(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].user = ctx["partner"]
        ctx["doc"].user_type = "partner"
        ctx["doc"].disabled = True
        ctx["doc"].save(update_fields=["user", "user_type", "disabled"])
        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentSiblingGroupTraversal:
    """Partner/broker must still download docs relinked to a sibling under the same holding."""

    def test_partner_downloads_doc_relinked_to_sibling_client(self, doc_on_shared_project, client):
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        parent = ClientCompany.objects.create(company="Group Holding AG")
        ctx["client_company"].holding = parent
        ctx["client_company"].save(update_fields=["holding"])
        sibling = ClientCompany.objects.create(
            company="Sibling GmbH",
            holding=parent,
        )
        ctx["doc"].client = sibling
        ctx["doc"].save(update_fields=["client"])

        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentBrokerGroupTraversal:
    """Broker keeps access when doc is relinked to a sister company with no own project row."""

    def test_broker_downloads_doc_relinked_to_sister_without_broker_company(self, doc_on_shared_project, client):
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        # Sister company — same holding, no broker_company set, no project row.
        sister = ClientCompany.objects.create(
            company="Sister GmbH",
            holding=ctx["client_company"],
        )
        ctx["doc"].client = sister
        ctx["doc"].save(update_fields=["client"])

        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentPartnerExclusionIsolation:
    """A partner's access must only depend on their own PartnerStatus row,
    not on other partners' rows on the same project."""

    def test_partner_not_excluded_still_sees_pdf_when_other_partner_excluded(self, doc_on_shared_project, client):
        from project.models import PartnerStatus

        ctx = doc_on_shared_project
        # Our partner has an active (not-excluded) status row.
        PartnerStatus.objects.create(
            project=ctx["project"],
            partner=ctx["partner"].leasing_company,
            excluded=False,
        )
        # A second partner on the same project is excluded.
        other_partner = create_partner()
        ctx["project"].invited_leasing_companies.add(other_partner.leasing_company)
        ctx["project"].leasing_companies.add(other_partner.leasing_company)
        PartnerStatus.objects.create(
            project=ctx["project"],
            partner=other_partner.leasing_company,
            excluded=True,
        )
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentSiblingClientAuthoredDraft:
    """Sibling clients may share reviewed docs, but NOT each other's client-authored drafts."""

    def test_sibling_client_cannot_download_unreviewed_client_authored_draft(self, doc_on_shared_project, client):
        from users.factories import create_client as make_client
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        parent = ClientCompany.objects.create(company="Tight Group AG")
        ctx["client_company"].holding = parent
        ctx["client_company"].save(update_fields=["holding"])
        sibling_b = ClientCompany.objects.create(
            company="Sibling B GmbH",
            holding=parent,
            broker_company=ctx["broker"].broker_company,
        )
        client_user_b = make_client(broker=ctx["broker"])
        client_user_b.client_company = sibling_b
        client_user_b.save(update_fields=["client_company"])

        # Client B uploads their own draft (client-authored, unreviewed).
        ctx["doc"].client = sibling_b
        ctx["doc"].user = client_user_b
        ctx["doc"].user_type = "client"
        ctx["doc"].reviewed = False
        ctx["doc"].save(update_fields=["client", "user", "user_type", "reviewed"])

        # Sibling A's client user must NOT see the draft.
        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentBrokerFallbackRespectsInactiveLink:
    """Broker fallback via client.broker_company must not bypass an explicit inactive link."""

    def test_broker_denied_when_client_inactive_even_if_owns_broker_company(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        # Standalone client (no holding) owned by the broker.
        standalone = ClientCompany.objects.create(
            company="Standalone Client GmbH",
            broker_company=ctx["broker"].broker_company,
        )
        ctx["doc"].client = standalone
        ctx["doc"].save(update_fields=["client"])
        # But it was added to the broker's project and explicitly toggled inactive.
        ProjectCompanyLink.objects.create(
            project=ctx["project"],
            client=standalone,
            is_active=False,
        )

        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentInactiveLinkScope:
    """Per-project inactive links must not act as a global deny — one active project suffices."""

    def test_client_sees_doc_when_inactive_on_one_project_but_active_on_another(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink

        ctx = doc_on_shared_project
        # Second project on the same client company, doc.client explicitly inactive there.
        other_project = create_project(
            broker_company=ctx["broker"].broker_company,
            client_company=ctx["client_company"],
        )
        ProjectCompanyLink.objects.create(
            project=other_project,
            client=ctx["client_company"],
            is_active=False,
        )
        # Original project does NOT have an inactive link, so client should still access.
        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_broker_sees_doc_when_inactive_on_one_project_but_active_on_another(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink

        ctx = doc_on_shared_project
        other_project = create_project(
            broker_company=ctx["broker"].broker_company,
            client_company=ctx["client_company"],
        )
        ProjectCompanyLink.objects.create(
            project=other_project,
            client=ctx["client_company"],
            is_active=False,
        )
        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentPartnerAuthoredWithExclusion:
    """Partner-authored docs must also honor PartnerStatus.excluded and inactive links."""

    def test_partner_author_gets_403_when_excluded_from_only_project(self, doc_on_shared_project, client):
        from project.models import PartnerStatus

        ctx = doc_on_shared_project
        # Doc now authored by partner.
        ctx["doc"].user = ctx["partner"]
        ctx["doc"].user_type = "partner"
        ctx["doc"].save(update_fields=["user", "user_type"])
        PartnerStatus.objects.create(
            project=ctx["project"],
            partner=ctx["partner"].leasing_company,
            excluded=True,
        )
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentClientSiblingInactiveLink:
    """Client sibling access must respect ProjectCompanyLink.is_active=False."""

    def test_client_blocked_on_sibling_marked_inactive_on_group_project(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        parent = ClientCompany.objects.create(company="Explicit Group AG")
        ctx["client_company"].holding = parent
        ctx["client_company"].save(update_fields=["holding"])
        sibling_b = ClientCompany.objects.create(
            company="Sibling B Inactive GmbH",
            holding=parent,
            broker_company=ctx["broker"].broker_company,
        )
        # doc now lives on sibling B.
        ctx["doc"].client = sibling_b
        ctx["doc"].save(update_fields=["client"])
        # Sibling B was explicitly toggled inactive on the group project.
        ProjectCompanyLink.objects.create(
            project=ctx["project"],
            client=sibling_b,
            is_active=False,
        )

        login_and_verify(ctx["client_user"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentNullAuthor:
    """Legacy docs with user=NULL must not leak to scoped users."""

    def test_partner_blocked_on_null_author_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].user = None
        ctx["doc"].save(update_fields=["user"])
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentInactiveCompanyLink:
    """Docs on a subsidiary toggled inactive for the project must 403 for partner/broker."""

    def test_partner_blocked_when_doc_client_inactive_on_project(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        subsidiary = ClientCompany.objects.create(
            company="Inactive Sub GmbH",
            broker_company=ctx["broker"].broker_company,
            holding=ctx["client_company"],
        )
        ctx["doc"].client = subsidiary
        ctx["doc"].save(update_fields=["client"])
        ProjectCompanyLink.objects.create(
            project=ctx["project"],
            client=subsidiary,
            is_active=False,
        )

        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403

    def test_broker_blocked_when_doc_client_inactive_on_project(self, doc_on_shared_project, client):
        from data_room.models import ProjectCompanyLink
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        subsidiary = ClientCompany.objects.create(
            company="Inactive Sub2 GmbH",
            holding=ctx["client_company"],
        )
        ctx["doc"].client = subsidiary
        ctx["doc"].save(update_fields=["client"])
        ProjectCompanyLink.objects.create(
            project=ctx["project"],
            client=subsidiary,
            is_active=False,
        )

        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentPartnerHoldingHierarchy:
    """Partner access must traverse client-company group for relinked docs."""

    def test_partner_can_download_doc_relinked_to_subsidiary(self, doc_on_shared_project, client):
        from users.models import ClientCompany

        ctx = doc_on_shared_project
        subsidiary = ClientCompany.objects.create(
            company="Sub GmbH",
            broker_company=ctx["broker"].broker_company,
            holding=ctx["client_company"],
        )
        ctx["doc"].client = subsidiary
        ctx["doc"].save(update_fields=["client"])

        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentSameRoleDisabled:
    """Same-role owners still see disabled docs (mirrors model check_permissions)."""

    def test_broker_can_download_disabled_broker_authored_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].disabled = True
        ctx["doc"].save(update_fields=["disabled"])
        login_and_verify(ctx["broker"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200

    def test_partner_can_download_disabled_partner_authored_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].user = ctx["partner"]
        ctx["doc"].user_type = "partner"
        ctx["doc"].disabled = True
        ctx["doc"].save(update_fields=["user", "user_type", "disabled"])
        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


def _page_url(doc):
    return reverse("data_room:client-doc-page", kwargs={"pk": doc.pk})


@pytest.mark.django_db
class TestClientDocumentPagePreview:
    """The preview endpoint must use the same scoped read check as the PDF download."""

    def test_unrelated_partner_gets_403_on_page_preview(self, doc_on_shared_project, client):
        outsider = create_partner()
        login_and_verify(outsider, client)
        response = client.get(_page_url(doc_on_shared_project["doc"]))
        assert response.status_code == 403

    def test_invited_partner_gets_page_preview(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["partner"], client)
        with patch(
            "data_room.views.api.protected_document.DocumentProcessor.process_single_page",
            return_value=({"path": "x"}, None, None, 1),
        ):
            response = client.get(_page_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentPartnerSelectedButNotInvited:
    """Partner visible via leasing_companies even before invited_leasing_companies sync."""

    def test_selected_partner_can_download_pdf_before_invite(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        # Remove the invitation, but keep selection via leasing_companies.
        ctx["project"].invited_leasing_companies.remove(ctx["partner"].leasing_company)
        assert ctx["partner"].leasing_company in ctx["project"].leasing_companies.all()

        login_and_verify(ctx["partner"], client)
        with _mock_pdf():
            response = client.get(_pdf_url(ctx["doc"]))
        assert response.status_code == 200


@pytest.mark.django_db
class TestClientDocumentOriginalRead:
    def test_unrelated_client_user_gets_403_on_original(self, doc_on_shared_project, client):
        outsider = create_client()
        login_and_verify(outsider, client)
        response = client.get(_original_url(doc_on_shared_project["doc"]))
        assert response.status_code == 403


@pytest.mark.django_db
class TestClientDocumentManageStaysRestricted:
    """Partners/clients must NOT gain access to destructive endpoints."""

    def test_partner_cannot_delete_client_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["partner"], client)
        response = client.post(reverse("data_room:client-doc-delete", kwargs={"pk": ctx["doc"].pk}))
        ctx["doc"].refresh_from_db()
        assert ProtectedClientDocument.objects.filter(pk=ctx["doc"].pk).exists()
        assert response.status_code in (200, 302)

    def test_partner_cannot_disable_client_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        login_and_verify(ctx["partner"], client)
        client.post(reverse("data_room:client-doc-disabled", kwargs={"pk": ctx["doc"].pk}))
        ctx["doc"].refresh_from_db()
        assert ctx["doc"].disabled is False

    def test_client_user_cannot_toggle_ai(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        ctx["doc"].use_ai = False
        ctx["doc"].save()
        login_and_verify(ctx["client_user"], client)
        client.post(reverse("data_room:client-doc-toggle-ai", kwargs={"pk": ctx["doc"].pk}))
        ctx["doc"].refresh_from_db()
        assert ctx["doc"].use_ai is False


@pytest.mark.django_db
class TestWritePermissionRequiresReadScope:
    """Open Question: _check_write_permission gave every broker unconditional
    destructive access to any client document. The fix gates write/destroy
    behind read-scope — a broker may only manage docs they could also read."""

    def test_unrelated_broker_cannot_delete_client_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        outsider = create_broker()
        login_and_verify(outsider, client)
        client.post(reverse("data_room:client-doc-delete", kwargs={"pk": ctx["doc"].pk}))
        assert ProtectedClientDocument.objects.filter(
            pk=ctx["doc"].pk
        ).exists(), "unrelated broker must NOT be able to delete docs of a client they have no project with"

    def test_unrelated_broker_cannot_disable_client_doc(self, doc_on_shared_project, client):
        ctx = doc_on_shared_project
        outsider = create_broker()
        login_and_verify(outsider, client)
        client.post(reverse("data_room:client-doc-disabled", kwargs={"pk": ctx["doc"].pk}))
        ctx["doc"].refresh_from_db()
        assert ctx["doc"].disabled is False

    def test_project_broker_can_delete_client_doc(self, doc_on_shared_project, client):
        """Positive: broker who IS on the project keeps destructive access."""
        ctx = doc_on_shared_project
        login_and_verify(ctx["broker"], client)
        client.post(reverse("data_room:client-doc-delete", kwargs={"pk": ctx["doc"].pk}))
        assert not ProtectedClientDocument.objects.filter(pk=ctx["doc"].pk).exists()


@pytest.mark.django_db
class TestProjectDocumentPreCheck:
    """Finding #4: ProtectedProjectDocument.check_permissions must share the
    same pre-check block as ProtectedClientDocument — is_authenticated,
    is_superuser/is_staff bypass, self.user + user_id guard, get_company()."""

    def _verified(self, user):
        user.is_verified = lambda: True
        return user

    def _make_project_doc(self, ctx):
        from django.core.files.base import ContentFile

        from data_room.models import ProtectedProjectDocument

        doc = ProtectedProjectDocument.objects.create(
            project=ctx["project"],
            name="pdoc",
            user=ctx["broker"],
            user_type="broker",
            reviewed=True,
            disabled=False,
        )
        doc.file.save("pdoc.pdf", ContentFile(b"%PDF-1.4"))
        return doc

    def test_superuser_allowed_even_without_type_admin(self, doc_on_shared_project):
        from users.factories import get_uuid_str
        from users.models import User

        doc = self._make_project_doc(doc_on_shared_project)
        su = User.objects.create_superuser(email=get_uuid_str() + "@ex.com", password="x")
        su.type = "broker"  # NOT admin
        su.save()
        assert doc.check_permissions(self._verified(su)) is True

    def test_staff_allowed_even_without_type_admin(self, doc_on_shared_project):
        from users.factories import get_uuid_str
        from users.models import User

        doc = self._make_project_doc(doc_on_shared_project)
        staff = User.objects.create(email=get_uuid_str() + "@ex.com", is_staff=True)
        staff.type = "broker"
        staff.save()
        assert doc.check_permissions(self._verified(staff)) is True

    def test_unauthenticated_user_denied(self, doc_on_shared_project):
        from django.contrib.auth.models import AnonymousUser

        doc = self._make_project_doc(doc_on_shared_project)
        anon = AnonymousUser()
        assert doc.check_permissions(anon) is False


@pytest.mark.django_db
class TestProjectDocumentPartnerExcluded:
    """ProtectedProjectDocument must honor PartnerStatus.excluded the same way
    ProtectedClientDocument does — a partner that was explicitly removed from
    a project cannot keep reading its project-level documents via direct URL.
    """

    def _verified(self, user):
        user.is_verified = lambda: True
        return user

    def _make_project_doc(self, ctx, user_type="broker", author=None):
        from django.core.files.base import ContentFile

        from data_room.models import ProtectedProjectDocument

        doc = ProtectedProjectDocument.objects.create(
            project=ctx["project"],
            name="pdoc",
            user=author or ctx["broker"],
            user_type=user_type,
            reviewed=True,
            disabled=False,
        )
        doc.file.save("pdoc.pdf", ContentFile(b"%PDF-1.4"))
        return doc

    def test_partner_excluded_from_project_denied_on_project_doc(self, doc_on_shared_project):
        from project.models import PartnerStatus

        ctx = doc_on_shared_project
        doc = self._make_project_doc(ctx)
        partner = self._verified(ctx["partner"])

        PartnerStatus.objects.create(
            project=ctx["project"],
            partner=partner.leasing_company,
            excluded=True,
        )
        assert (
            doc.check_permissions(partner) is False
        ), "partner explicitly excluded from project must NOT read its project docs"

    def test_partner_not_excluded_still_allowed_on_project_doc(self, doc_on_shared_project):
        """Positive counterpart: no excluded marker → existing allow rules still hold."""
        ctx = doc_on_shared_project
        doc = self._make_project_doc(ctx)
        partner = self._verified(ctx["partner"])
        assert doc.check_permissions(partner) is True

    def test_partner_excluded_cannot_read_own_upload(self, doc_on_shared_project):
        """Consistency with client-doc branch: excluded partners lose access
        even to their own uploaded documents on that project."""
        from project.models import PartnerStatus

        ctx = doc_on_shared_project
        partner = self._verified(ctx["partner"])
        doc = self._make_project_doc(ctx, user_type="partner", author=partner)

        PartnerStatus.objects.create(
            project=ctx["project"],
            partner=partner.leasing_company,
            excluded=True,
        )
        assert doc.check_permissions(partner) is False, "excluded partner must lose access even to their own uploads"
