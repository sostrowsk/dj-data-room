"""Tests for the package-local, policy-based view decorators (Plan Phase 4A, step A6).

``data_room.decorators`` replaces the host's ``leasing.decorators`` for all
data_room-internal usages: pk resolution, ``request.project`` /
``request.protected_document`` binding, login redirect for anonymous users,
``PermissionDenied`` for authenticated users without access and the
``htmx_required`` gate — all permission checks routed through ``get_policy()``.
"""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from data_room.decorators import project_permission_required, protected_document_permission_required
from data_room.tests.factories import ProtectedDocumentFactory
from users.factories import create_admin, create_client


def _verified(user):
    """Bypass OTP for unit-level decorator checks (established test pattern)."""
    user.is_verified = lambda: True
    return user


@project_permission_required
def _project_view(request, pk):
    return HttpResponse("project-ok")


@project_permission_required(htmx_required=True)
def _project_htmx_view(request, pk):
    return HttpResponse("project-htmx-ok")


@protected_document_permission_required
def _document_view(request, pk):
    return HttpResponse("document-ok")


class ProjectPermissionRequiredTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.document = ProtectedDocumentFactory()
        self.project = self.document.project

    def test_redirects_anonymous_user_to_login(self):
        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = AnonymousUser()

        response = _project_view(request, pk=self.project.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)
        self.assertIn(f"next={request.path}", response.url)

    def test_login_redirect_honors_login_url_setting(self):
        """A9: anonymous redirects use ``settings.LOGIN_URL``, not the
        hardcoded ``login`` URL name."""
        from django.test import override_settings

        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = AnonymousUser()

        with override_settings(LOGIN_URL="/custom-login/"):
            response = _project_view(request, pk=self.project.pk)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/custom-login/?next="))

    def test_denies_foreign_client_user(self):
        foreign_client = create_client()
        foreign_client.is_verified = lambda: True  # bypass OTP for unit-level checks
        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = foreign_client

        with self.assertRaises(PermissionDenied):
            _project_view(request, pk=self.project.pk)

    def test_allows_admin_and_binds_request_project(self):
        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = _verified(create_admin())

        response = _project_view(request, pk=self.project.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.project, self.project)

    def test_resolves_pk_from_positional_args(self):
        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = _verified(create_admin())

        response = _project_view(request, self.project.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.project, self.project)

    def test_htmx_required_rejects_non_htmx_request(self):
        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = _verified(create_admin())
        request.htmx = False

        response = _project_htmx_view(request, pk=self.project.pk)

        self.assertEqual(response.status_code, 405)

    def test_htmx_required_allows_htmx_request(self):
        request = self.factory.get(f"/data-room/{self.project.pk}/")
        request.user = _verified(create_admin())
        request.htmx = True

        response = _project_htmx_view(request, pk=self.project.pk)

        self.assertEqual(response.status_code, 200)


class ProtectedDocumentPermissionRequiredTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.document = ProtectedDocumentFactory()

    def test_redirects_anonymous_user_to_login(self):
        request = self.factory.get(f"/data-room/document/{self.document.pk}/")
        request.user = AnonymousUser()

        response = _document_view(request, pk=self.document.pk)

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)
        self.assertIn(f"next={request.path}", response.url)

    def test_denies_foreign_client_user(self):
        foreign_client = create_client()
        foreign_client.is_verified = lambda: True  # bypass OTP for unit-level checks
        request = self.factory.get(f"/data-room/document/{self.document.pk}/")
        request.user = foreign_client

        with self.assertRaises(PermissionDenied):
            _document_view(request, pk=self.document.pk)

    def test_allows_admin_and_binds_request_protected_document(self):
        request = self.factory.get(f"/data-room/document/{self.document.pk}/")
        request.user = _verified(create_admin())

        response = _document_view(request, pk=self.document.pk)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.protected_document, self.document)
