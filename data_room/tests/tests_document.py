import os
import shutil
import tempfile

from django.test import TestCase
from django.urls import reverse

from users.factories import create_admin, create_client, login_and_verify


class ProtectedFileViewTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.client_user = create_client()
        self.temp_dir = tempfile.mkdtemp()
        self.test_file_path = os.path.join(self.temp_dir, "test_image.png")
        with open(self.test_file_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
        super().tearDown()

    def test_access_denied_for_non_superuser(self):
        login_and_verify(self.client_user, self.client)
        response = self.client.get(reverse("for ", kwargs={"path": "test_image.png"}))

        self.assertEqual(response.status_code, 403)

    def test_successful_file_serve_for_superuser(self):
        login_and_verify(self.admin, self.client)
        with self.settings(PROTECTED_MEDIA_ROOT=self.temp_dir, PROTECTED_MEDIA_SERVER="nginx"):
            response = self.client.get(reverse("for ", kwargs={"path": "test_image.png"}))
            self.assertEqual(response.status_code, 200)
            self.assertIn("X-Accel-Redirect", response)

    def test_file_download(self):
        login_and_verify(self.admin, self.client)

        with self.settings(
            PROTECTED_MEDIA_AS_DOWNLOADS=True,
            PROTECTED_MEDIA_ROOT=self.temp_dir,
            PROTECTED_MEDIA_SERVER="nginx",
        ):
            response = self.client.get(reverse("for ", kwargs={"path": "test_image.png"}))
            self.assertEqual(response.status_code, 200)
            self.assertIn("Content-Disposition", response.headers)
            self.assertTrue(response["Content-Disposition"].startswith("attachment;"))

    def test_file_not_found(self):
        login_and_verify(self.admin, self.client)
        with self.settings(PROTECTED_MEDIA_ROOT=self.temp_dir):
            response = self.client.get(reverse("for ", kwargs={"path": "non-existent.txt"}))
            self.assertEqual(response.status_code, 404)

    def test_django_server(self):
        login_and_verify(self.admin, self.client)
        with self.settings(PROTECTED_MEDIA_SERVER="django", PROTECTED_MEDIA_ROOT=self.temp_dir):
            response = self.client.get(reverse("for ", kwargs={"path": "test_image.png"}))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("X-Accel-Redirect", response.headers)
            self.assertTrue(response["Content-Disposition"].startswith("inline;"))
            self.assertEqual(response["Content-Type"], "image/png")
