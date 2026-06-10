from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django_otp.plugins.otp_static.models import StaticDevice

from data_room.models import ProtectedProjectDocument
from project.tests.factories import ProjectFactory
from users.tests.factories import BrokerFactory, ClientFactory


@override_settings(LANGUAGE_CODE="de")
class DocumentSortingTests(TestCase):
    def setUp(self):
        self.client_user = ClientFactory()
        self.broker_user = BrokerFactory()
        self.project = ProjectFactory(
            client_company=self.client_user.client_company,
            broker_company=self.broker_user.broker_company,
        )

        # Set up OTP devices
        for user in [self.client_user, self.broker_user]:
            device = StaticDevice.objects.create(user=user, name="default")
            device.token_set.create(token="testtoken123")

        # Create test documents with different names and dates
        self.doc1 = ProtectedProjectDocument.objects.create(
            project=self.project,
            user=self.broker_user,
            name="Alpha Document",
            user_type="broker",
            user_company=str(self.broker_user.broker_company),
            reviewed=True,
        )
        self.doc1.file.save("alpha.pdf", ContentFile(b"content"))

        self.doc2 = ProtectedProjectDocument.objects.create(
            project=self.project,
            user=self.broker_user,
            name="Beta Document",
            user_type="broker",
            user_company=str(self.broker_user.broker_company),
            reviewed=True,
        )
        self.doc2.file.save("beta.pdf", ContentFile(b"content"))

        self.doc3 = ProtectedProjectDocument.objects.create(
            project=self.project,
            user=self.broker_user,
            name="Gamma Document",
            user_type="broker",
            user_company=str(self.broker_user.broker_company),
            reviewed=True,
        )
        self.doc3.file.save("gamma.pdf", ContentFile(b"content"))

        # Force different creation dates
        self.doc1.date_created = "2023-01-01 10:00:00"
        self.doc1.save()
        self.doc2.date_created = "2023-02-01 10:00:00"
        self.doc2.save()
        self.doc3.date_created = "2023-03-01 10:00:00"
        self.doc3.save()

    def _login_with_otp(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["_auth_user_id"] = str(user.pk)
        session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
        session["_auth_user_hash"] = user.get_session_auth_hash()
        device = user.staticdevice_set.first()
        session["otp_device_id"] = device.persistent_id
        session.save()

    def test_documents_sorted_by_name_ascending(self):
        self._login_with_otp(self.broker_user)
        url = reverse("project:detail", kwargs={"pk": self.project.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        broker_documents = list(response.context["broker_documents"])
        self.assertEqual(len(broker_documents), 3)
        self.assertEqual(broker_documents[0].name, "Alpha Document")
        self.assertEqual(broker_documents[1].name, "Beta Document")
        self.assertEqual(broker_documents[2].name, "Gamma Document")

    def test_documents_sorted_by_name_descending(self):
        self._login_with_otp(self.broker_user)
        url = reverse("project:detail", kwargs={"pk": self.project.pk})
        response = self.client.get(url + "?sort_documents=-name")

        self.assertEqual(response.status_code, 200)
        broker_documents = list(response.context["broker_documents"])
        self.assertEqual(len(broker_documents), 3)
        self.assertEqual(broker_documents[0].name, "Gamma Document")
        self.assertEqual(broker_documents[1].name, "Beta Document")
        self.assertEqual(broker_documents[2].name, "Alpha Document")

    def test_documents_sorted_by_date_oldest_first(self):
        self._login_with_otp(self.broker_user)
        url = reverse("project:detail", kwargs={"pk": self.project.pk})
        response = self.client.get(url + "?sort_documents=date")

        self.assertEqual(response.status_code, 200)
        broker_documents = list(response.context["broker_documents"])
        self.assertEqual(len(broker_documents), 3)
        self.assertEqual(broker_documents[0].name, "Alpha Document")  # Oldest
        self.assertEqual(broker_documents[1].name, "Beta Document")
        self.assertEqual(broker_documents[2].name, "Gamma Document")  # Newest

    def test_documents_sorted_by_date_newest_first(self):
        self._login_with_otp(self.broker_user)
        url = reverse("project:detail", kwargs={"pk": self.project.pk})
        response = self.client.get(url + "?sort_documents=-date")

        self.assertEqual(response.status_code, 200)
        broker_documents = list(response.context["broker_documents"])
        self.assertEqual(len(broker_documents), 3)
        self.assertEqual(broker_documents[0].name, "Gamma Document")  # Newest
        self.assertEqual(broker_documents[1].name, "Beta Document")
        self.assertEqual(broker_documents[2].name, "Alpha Document")  # Oldest
