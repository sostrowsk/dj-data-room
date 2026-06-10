from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice

from data_room.helpers import get_document_list_context
from data_room.tests.factories import ProtectedDocumentFactory
from project.models import Project
from project.tests.project_utils import create_project
from users.factories import create_broker, create_client, create_partner, login_and_verify

UserModel = get_user_model()


class ProjectHxEmptyViewTests(TestCase):
    def setUp(self):
        self.client_user = create_client()
        self.broker_user = create_broker()
        self.project = create_project(client_company=self.client_user.client_company)

    def tearDown(self):
        Project.objects.all().delete()
        UserModel.objects.all().delete()

    def test_hx_empty_view(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(
            reverse("data_room:hx-empty", args=[self.project.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "data_room/_show_protected_documents.html")

    def test_no_htmx_request(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(reverse("data_room:hx-empty", args=[self.project.pk]))

        self.assertEqual(response.status_code, 403)


class DocumentBucketFilteringMixin:
    """Shared fixtures: a project with reviewed + draft documents per role."""

    def setUp(self):
        self.client_user = create_client()
        self.broker_user = create_broker()
        self.partner_user = create_partner()
        self.other_partner = create_partner()
        self.project = create_project(
            client_company=self.client_user.client_company,
            broker_company=self.broker_user.broker_company,
            leasing_company=self.partner_user.leasing_company,
        )
        self.broker_doc_reviewed = ProtectedDocumentFactory(
            project=self.project,
            user=self.broker_user,
            user_type="broker",
            user_company=self.broker_user.broker_company.company,
            reviewed=True,
        )
        self.broker_doc_draft = ProtectedDocumentFactory(
            project=self.project,
            user=self.broker_user,
            user_type="broker",
            user_company=self.broker_user.broker_company.company,
            reviewed=False,
        )
        self.client_doc_draft = ProtectedDocumentFactory(
            project=self.project, user=self.client_user, user_type="client", reviewed=False
        )
        self.partner_doc_draft = ProtectedDocumentFactory(
            project=self.project,
            user=self.partner_user,
            user_type="partner",
            user_company=self.partner_user.leasing_company.company,
            reviewed=False,
        )
        self.other_partner_doc_reviewed = ProtectedDocumentFactory(
            project=self.project,
            user=self.other_partner,
            user_type="partner",
            user_company=self.other_partner.leasing_company.company,
            reviewed=True,
        )

    @staticmethod
    def _ids(documents):
        return {document.pk for document in documents}


class HxEmptyBucketFilteringSnapshotTests(DocumentBucketFilteringMixin, TestCase):
    """Snapshot of the hx_empty bucket filtering (drafts restricted) before
    it moves into the permission policy (plan step A5 commit 4)."""

    def _get(self, user):
        login_and_verify(user, self.client)
        return self.client.get(
            reverse("data_room:hx-empty", args=[self.project.pk]),
            HTTP_HX_REQUEST="true",
        )

    def test_client_user_sees_own_type_drafts_but_only_reviewed_foreign_docs(self):
        response = self._get(self.client_user)
        self.assertEqual(self._ids(response.context["broker_documents"]), {self.broker_doc_reviewed.pk})
        self.assertEqual(self._ids(response.context["client_documents"]), {self.client_doc_draft.pk})
        self.assertEqual(self._ids(response.context["partner_documents"]), {self.other_partner_doc_reviewed.pk})
        self.assertTrue(response.context["not_partner"])

    def test_broker_user_sees_all_buckets_unfiltered(self):
        response = self._get(self.broker_user)
        self.assertEqual(
            self._ids(response.context["broker_documents"]),
            {self.broker_doc_reviewed.pk, self.broker_doc_draft.pk},
        )
        self.assertEqual(self._ids(response.context["client_documents"]), {self.client_doc_draft.pk})
        self.assertEqual(
            self._ids(response.context["partner_documents"]),
            {self.partner_doc_draft.pk, self.other_partner_doc_reviewed.pk},
        )
        self.assertTrue(response.context["not_partner"])

    def test_partner_user_sees_only_own_company_partner_docs_including_drafts(self):
        response = self._get(self.partner_user)
        self.assertEqual(self._ids(response.context["broker_documents"]), {self.broker_doc_reviewed.pk})
        self.assertEqual(self._ids(response.context["client_documents"]), set())
        self.assertEqual(self._ids(response.context["partner_documents"]), {self.partner_doc_draft.pk})
        self.assertFalse(response.context["not_partner"])


class GetDocumentListContextSnapshotTests(DocumentBucketFilteringMixin, TestCase):
    """Snapshot of get_document_list_context: NO draft restriction (unlike
    hx_empty), but the partner author scope still applies."""

    @staticmethod
    def _verify(user):
        StaticDevice.objects.get_or_create(user=user)
        return user

    def test_client_user_sees_broker_drafts_in_list_context(self):
        context = get_document_list_context(self.project, self._verify(self.client_user))
        self.assertEqual(
            self._ids(context["broker_documents"]),
            {self.broker_doc_reviewed.pk, self.broker_doc_draft.pk},
        )
        self.assertEqual(self._ids(context["client_documents"]), {self.client_doc_draft.pk})
        self.assertEqual(
            self._ids(context["partner_documents"]),
            {self.partner_doc_draft.pk, self.other_partner_doc_reviewed.pk},
        )
        self.assertTrue(context["not_partner"])

    def test_partner_user_partner_docs_scoped_to_own_company(self):
        context = get_document_list_context(self.project, self._verify(self.partner_user))
        self.assertEqual(
            self._ids(context["broker_documents"]),
            {self.broker_doc_reviewed.pk, self.broker_doc_draft.pk},
        )
        self.assertEqual(self._ids(context["partner_documents"]), {self.partner_doc_draft.pk})
        self.assertFalse(context["not_partner"])
