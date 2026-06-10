from django.test import TestCase
from django.urls import reverse

from data_room.models import ProtectedProjectDocument
from project.tests.project_utils import create_project
from users.factories import create_broker, create_client, create_partner, login_and_verify


class ApiDocumentPageViewTests(TestCase):
    def setUp(self):
        self.broker = create_broker()
        self.client_user = create_client()
        self.partner_user = create_partner()
        self.project = create_project()
        self.project_with_client_company = create_project(client_company=self.client_user.client_company)
        self.document = ProtectedProjectDocument.objects.create(
            user=self.client_user,
            name="test",
            file="document/test_image.png",
            project=self.project_with_client_company,
            user_company=self.client_user.client_company,
            user_type="client",
        )
        self.document_pdf = ProtectedProjectDocument.objects.create(
            user=self.client_user,
            name="test",
            file="document/test_document.pdf",
            project=self.project_with_client_company,
            user_company=self.client_user.client_company,
            user_type="client",
        )

    def test_get_page_image(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(reverse("data_room:api-page", args=[self.document.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["protected_document"], self.document)

    def test_get_page_pdf(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(reverse("data_room:api-page", args=[self.document_pdf.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["protected_document"], self.document_pdf)

        response = self.client.get(reverse("data_room:api-page", args=[self.document_pdf.pk]), {"page": 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["protected_document"], self.document_pdf)

        response = self.client.get(reverse("data_room:api-page", args=[self.document_pdf.pk]), {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["protected_document"], self.document_pdf)

    def test_invalid_document(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(
            reverse(
                "data_room:api-page",
                args=[
                    999,
                ],
            )
        )
        self.assertEqual(response.status_code, 404)

    def test_client_without_project(self):
        login_and_verify(self.client_user, self.client)
        document = ProtectedProjectDocument.objects.create(
            user=self.client_user,
            name="test",
            file="document/test_image.png",
            project=self.project,
            user_company=self.client_user.client_company,
            user_type="client",
        )
        response = self.client.get(reverse("data_room:api-page", args=[document.pk]))
        self.assertEqual(response.status_code, 403)

    def test_no_login(self):
        response = self.client.get(reverse("data_room:api-page", args=[self.document.pk]))
        self.assertEqual(response.status_code, 302)
