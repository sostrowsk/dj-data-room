"""Regression test for: ``data_room:create`` redirected to the non-existent
URL name ``project_details`` after a successful upload (NoReverseMatch).

Plan Phase 4A, step A9: the redirect is unified onto the configurable
``DATA_ROOM_PROJECT_DETAIL_URL`` (default ``project:detail``).
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice

from data_room.models import ProtectedProjectDocument
from project.tests.factories import ProjectFactory
from users.tests.factories import ClientFactory


class CreateViewRedirectTests(TestCase):
    def test_successful_upload_redirects_to_project_detail(self):
        user = ClientFactory()
        project = ProjectFactory(client_company=user.client_company)

        device = StaticDevice.objects.create(user=user, name="default")
        device.token_set.create(token="testtoken123")
        self.client.force_login(user)
        session = self.client.session
        session["otp_device_id"] = device.persistent_id
        session.save()

        upload = SimpleUploadedFile("test.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self.client.post(
            reverse("data_room:create", kwargs={"pk": project.pk}),
            {"name": "Testdokument", "file": upload},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("project:detail", kwargs={"pk": project.pk}))
        self.assertTrue(ProtectedProjectDocument.objects.filter(project=project, user=user).exists())
