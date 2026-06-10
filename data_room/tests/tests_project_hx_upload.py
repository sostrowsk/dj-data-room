from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from data_room.models import ProtectedClientDocument, ProtectedProjectDocument
from project.models import Project
from project.tests.project_utils import create_project
from users.factories import create_broker, create_client, login_and_verify
from users.models import User


class ProjectHxUploadFormTests(TestCase):
    def setUp(self):
        self.client_user = create_client()
        self.broker_user = create_broker()
        self.project = create_project()
        self.project_with_client_company = create_project(client_company=self.client_user.client_company)
        image = Image.new("RGB", (100, 100), "white")
        image_io = BytesIO()
        image.save(image_io, format="PNG")
        self.image_content = image_io.getvalue()

    def _get_document_data(self, filename="Test Image.png"):
        """Create fresh document data for each test (files can only be uploaded once)."""
        return {
            "files": SimpleUploadedFile(filename, self.image_content, content_type="image/png"),
        }

    def tearDown(self):
        Project.objects.all().delete()
        User.objects.all().delete()

    def test_unauthenticated_redirect(self):
        response = self.client.get(reverse("data_room:hx-upload-project", args=[self.project.pk]))

        self.assertEqual(response.status_code, 302)

    def test_valid_data(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.post(
            reverse("data_room:hx-upload-project", args=[self.project_with_client_company.pk]),
            data=self._get_document_data("Test Image.png"),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Image")

        # Check that the document is associated with the correct project.
        project_documents = ProtectedProjectDocument.objects.filter(project=self.project_with_client_company)
        self.assertEqual(project_documents.count(), 1)

    def test_client_without_client_company_ok(self):
        # client company is set in view, if not set
        login_and_verify(self.client_user, self.client)
        self.project.client_company = None
        self.project.save()
        response = self.client.post(
            reverse("data_room:hx-upload-project", args=[self.project.pk]),
            data=self._get_document_data(),
            HTTP_HX_Request="true",
        )

        # Client cannot access project without a client_company
        self.assertEqual(response.status_code, 403)

    def test_broker_cannot_upload_to_client_project(self):
        login_and_verify(self.broker_user, self.client)
        response = self.client.post(
            reverse("data_room:hx-upload-project", args=[self.project_with_client_company.pk]),
            data=self._get_document_data(),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 403)

    def test_not_hx_request_forbidden(self):

        login_and_verify(self.client_user, self.client)
        response = self.client.get(
            reverse("data_room:hx-upload-project", args=(self.project_with_client_company.id,)),
        )

        self.assertEqual(response.status_code, 405)

    def test_get_request(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(
            reverse("data_room:hx-upload-project", args=(self.project_with_client_company.id,)),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ProtectedProjectDocument.objects.filter(project=self.project_with_client_company).count(),
            0,
        )

    def test_invalid_data(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.post(
            reverse("data_room:hx-upload-project", args=[self.project_with_client_company.pk]),
            data={},
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            ProtectedProjectDocument.objects.filter(project=self.project_with_client_company).count(),
            0,
        )

    def test_name_derived_from_filename(self):
        """Document name is derived from the uploaded filename."""
        login_and_verify(self.client_user, self.client)
        response = self.client.post(
            reverse("data_room:hx-upload-project", args=(self.project_with_client_company.id,)),
            data=self._get_document_data("test_image.png"),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        project_documents = ProtectedProjectDocument.objects.filter(project=self.project_with_client_company)
        self.assertEqual(project_documents.first().name, "test_image")


class ClientHxUploadFormTests(TestCase):
    def setUp(self):
        self.client_user = create_client()
        self.broker_user = create_broker()
        self.project_with_client_company = create_project(client_company=self.client_user.client_company)
        self.project_without_client_company = create_project()
        self.project_without_client_company.client_company = None
        self.project_without_client_company.save()
        image = Image.new("RGB", (100, 100), "white")
        image_io = BytesIO()
        image.save(image_io, format="PNG")
        self.image_content = image_io.getvalue()

    def _get_document_data(self, filename="Test Image.png"):
        return {
            "files": SimpleUploadedFile(filename, self.image_content, content_type="image/png"),
        }

    def tearDown(self):
        Project.objects.all().delete()
        User.objects.all().delete()

    def test_client_upload_creates_client_document(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.post(
            reverse("data_room:hx-upload-client", args=[self.project_with_client_company.pk]),
            data=self._get_document_data("Client Doc.png"),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        client_docs = ProtectedClientDocument.objects.filter(client=self.client_user.client_company)
        self.assertEqual(client_docs.count(), 1)
        self.assertEqual(client_docs.first().name, "Client Doc")

    def test_get_request_shows_form(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(
            reverse("data_room:hx-upload-client", args=[self.project_with_client_company.pk]),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uploadForm")

    def test_no_client_company_shows_error(self):
        """Broker accessing client upload on project without client_company gets friendly error."""
        login_and_verify(self.broker_user, self.client)
        # broker_user's broker_company must own this project for permission
        self.project_without_client_company.broker_company = self.broker_user.broker_company
        self.project_without_client_company.save()
        response = self.client.get(
            reverse("data_room:hx-upload-client", args=[self.project_without_client_company.pk]),
            HTTP_HX_Request="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kein Unternehmen")
