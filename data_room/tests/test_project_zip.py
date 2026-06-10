import unittest
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice
from progress.models import TaskProgress

from data_room.models import ProjectZip, ProtectedProjectDocument
from project.tests.factories import ProjectFactory
from users.tests.factories import BrokerFactory, ClientFactory

User = get_user_model()


@override_settings(LANGUAGE_CODE="de")
class ProjectZipViewTests(TestCase):
    def setUp(self):
        self.client_user = ClientFactory()
        self.broker_user = BrokerFactory()
        self.project = ProjectFactory(
            client_company=self.client_user.client_company,
            broker_company=self.broker_user.broker_company,
        )

        # Set up OTP devices for users
        for user in [self.client_user, self.broker_user]:
            device = StaticDevice.objects.create(user=user, name="default")
            device.token_set.create(token="testtoken123")

    def _login_with_otp(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["_auth_user_id"] = str(user.pk)
        session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
        session["_auth_user_hash"] = user.get_session_auth_hash()
        # Mark OTP as verified
        device = user.staticdevice_set.first()
        session["otp_device_id"] = device.persistent_id
        session.save()

    def test_start_project_zip_generation_requires_auth(self):
        url = reverse("data_room:start-project-zip", kwargs={"pk": self.project.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_start_project_zip_generation_requires_permission(self):
        other_user = ClientFactory()
        # Create OTP device for new user
        device = StaticDevice.objects.create(user=other_user, name="default")
        device.token_set.create(token="testtoken123")
        self._login_with_otp(other_user)
        url = reverse("data_room:start-project-zip", kwargs={"pk": self.project.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    @patch("data_room.views.api.project_zip.project_zip_task.delay")
    def test_start_project_zip_generation_success(self, mock_task):
        self._login_with_otp(self.client_user)
        url = reverse("data_room:start-project-zip", kwargs={"pk": self.project.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        mock_task.assert_called_once_with(self.project.id, self.client_user.id)

    @patch("data_room.views.api.project_zip.project_zip_task.delay")
    def test_start_project_zip_deletes_old_zips(self, mock_task):
        old_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.client_user,
            status="completed",
        )
        old_zip.zip_file.save("old.zip", ContentFile(b"old content"))

        self._login_with_otp(self.client_user)
        url = reverse("data_room:start-project-zip", kwargs={"pk": self.project.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectZip.objects.filter(id=old_zip.id).exists())

    def test_download_project_zip_requires_auth(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.client_user,
            status="completed",
        )
        url = reverse("data_room:download-project-zip", kwargs={"pk": project_zip.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_download_project_zip_success(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.client_user,
            status="completed",
        )
        project_zip.zip_file.save("test.zip", ContentFile(b"test content"))

        self._login_with_otp(self.client_user)
        url = reverse("data_room:download-project-zip", kwargs={"pk": project_zip.pk})
        response = self.client.get(url + "?download=true")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_download_project_zip_with_unicode_filename(self):
        self.project.name = "Projekt München 377"
        self.project.save()

        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.client_user,
            status="completed",
        )
        unicode_filename = "Projekt München 377_20240101.zip"
        project_zip.zip_file.save(unicode_filename, ContentFile(b"test content"))

        self._login_with_otp(self.client_user)
        url = reverse("data_room:download-project-zip", kwargs={"pk": project_zip.pk})
        response = self.client.get(url + "?download=true")

        self.assertEqual(response.status_code, 200)
        disposition = response["Content-Disposition"]
        self.assertIn("attachment", disposition)
        self.assertIn("filename*=utf-8''Projekt_M%C3%BCnchen_377_20240101", disposition)
        self.assertIn(".zip", disposition)

    def test_download_project_zip_sends_downloaded_signal(self):
        """A9: the download view sends ``project_zip_downloaded`` instead of
        writing the History entry itself (the host connects a receiver)."""
        from data_room.signals import project_zip_downloaded

        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.client_user,
            status="completed",
        )
        project_zip.zip_file.save("test.zip", ContentFile(b"test content"))
        self._login_with_otp(self.client_user)

        received = []

        def listener(sender, **kwargs):
            received.append(kwargs)

        project_zip_downloaded.connect(listener)
        try:
            url = reverse("data_room:download-project-zip", kwargs={"pk": project_zip.pk})
            response = self.client.get(url + "?download=true")
        finally:
            project_zip_downloaded.disconnect(listener)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["user"], self.client_user)
        self.assertEqual(received[0]["project"], self.project)

    def test_download_project_zip_deletes_after_complete(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.client_user,
            status="completed",
        )
        project_zip.zip_file.save("test.zip", ContentFile(b"test content"))

        self._login_with_otp(self.client_user)
        url = reverse("data_room:download-project-zip", kwargs={"pk": project_zip.pk})
        response = self.client.get(url + "?complete=1")

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectZip.objects.filter(id=project_zip.id).exists())

    def test_project_zip_progress_view(self):
        self._login_with_otp(self.client_user)
        url = reverse("data_room:project-zip-progress", kwargs={"pk": self.project.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "data_room/project_zip_progress.html")
        self.assertIn("project", response.context)
        self.assertEqual(response.context["project"], self.project)

    def test_hx_project_zip_button(self):
        self._login_with_otp(self.client_user)
        url = reverse("data_room:hx-project-zip-button", kwargs={"pk": self.project.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "data_room/_zip_button.html")


@override_settings(LANGUAGE_CODE="de", CELERY_TASK_ALWAYS_EAGER=True)
class ProjectZipTaskTests(TestCase):
    def setUp(self):
        self.client_user = ClientFactory()
        self.broker_user = BrokerFactory()
        self.project = ProjectFactory(
            client_company=self.client_user.client_company,
            broker_company=self.broker_user.broker_company,
        )

    @patch("zipfile.ZipFile")
    @patch("builtins.open", create=True)
    @unittest.skip("Complex mocking required for task")
    def test_project_zip_task_creates_zip(self, mock_open, mock_zipfile):
        doc = ProtectedProjectDocument.objects.create(
            project=self.project,
            user=self.broker_user,
            name="Test document",
            user_company=str(self.broker_user.broker_company),
        )
        doc.file.save("test.pdf", ContentFile(b"test content"))
        doc.original.save("test.pdf", ContentFile(b"test content"))

        mock_zip = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_zip
        mock_file = MagicMock()
        mock_open.return_value.__enter__.return_value = mock_file

        from data_room.tasks.project_zip import project_zip_task

        result = project_zip_task(self.project.id, self.client_user.id)

        self.assertTrue(result)
        project_zip = ProjectZip.objects.get(project=self.project, user=self.client_user)
        self.assertEqual(project_zip.status, "completed")

    @unittest.skip("Complex mocking required for task")
    def test_project_zip_task_tracks_progress(self):
        doc = ProtectedProjectDocument.objects.create(
            project=self.project,
            user=self.broker_user,
            name="Test document",
            user_company=str(self.broker_user.broker_company),
        )
        doc.file.save("test.pdf", ContentFile(b"test content"))
        doc.original.save("test.pdf", ContentFile(b"test content"))

        with patch("data_room.tasks.project_zip.zipfile.ZipFile"):
            from data_room.tasks.project_zip import project_zip_task

            project_zip_task(self.project.id, self.client_user.id)

        task_progress = TaskProgress.objects.filter(
            user=self.client_user,
            task_type="Download ZIP generation",
            task_object_id=str(self.project.id),
        ).first()

        self.assertIsNotNone(task_progress)
        self.assertEqual(task_progress.status, "completed")


@override_settings(LANGUAGE_CODE="de")
class ProjectZipModelTests(TestCase):
    def setUp(self):
        self.user = ClientFactory()
        self.project = ProjectFactory(client_company=self.user.client_company)

    def test_project_zip_str(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="completed",
        )
        expected = f"ZIP for {self.project.name} ({project_zip.date_created.strftime('%Y-%m-%d')})"
        self.assertEqual(str(project_zip), expected)

    def test_project_zip_filename(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="completed",
        )
        project_zip.zip_file.save("test.zip", ContentFile(b"test"))
        # Django adds random suffix to uploaded files
        self.assertTrue(project_zip.filename().startswith("test"))
        self.assertTrue(project_zip.filename().endswith(".zip"))

    def test_project_zip_user_company(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="completed",
        )
        company = ProjectZip.user_company(project_zip)
        self.assertEqual(company, str(self.user.client_company))

    def test_project_zip_file_deleted_on_model_delete(self):
        project_zip = ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="completed",
        )
        project_zip.zip_file.save("test.zip", ContentFile(b"test"))

        with patch.object(project_zip.zip_file, "delete") as mock_delete:
            project_zip.delete()
            mock_delete.assert_called_once_with(save=False)
