"""Tests for data_room's host-model indirection (Plan Phase 4A, step A7).

``data_room.conf`` resolves the host's Project/ClientCompany models lazily
via settings (defaults match the leasing monorepo), the model FK strings use
the same getattr pattern (migration byte-stability) and the ``user_type``
choices are a frozen copy guarded against drift from the host user model.
"""

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from data_room import conf
from data_room.models import ProjectCompanyLink, ProtectedClientDocument, ProtectedProjectDocument
from data_room.models.choices import USER_TYPE_CHOICES
from data_room.models.project_zip import ProjectZip


class ConfResolverTests(TestCase):
    def test_get_project_model_defaults_to_project_project(self):
        self.assertIs(conf.get_project_model(), apps.get_model("project", "Project"))

    def test_get_client_company_model_defaults_to_users_clientcompany(self):
        self.assertIs(conf.get_client_company_model(), apps.get_model("users", "ClientCompany"))

    @override_settings(DATA_ROOM_PROJECT_MODEL="auth.Group")
    def test_get_project_model_respects_setting_override(self):
        self.assertIs(conf.get_project_model(), apps.get_model("auth", "Group"))

    @override_settings(DATA_ROOM_CLIENT_COMPANY_MODEL="auth.Group")
    def test_get_client_company_model_respects_setting_override(self):
        self.assertIs(conf.get_client_company_model(), apps.get_model("auth", "Group"))


class ModelFkTargetTests(SimpleTestCase):
    """FK targets stay byte-identical to the previous literal strings."""

    def _remote_label(self, model, field_name):
        return model._meta.get_field(field_name).remote_field.model._meta.label

    def test_project_fks_point_to_configured_project_model(self):
        self.assertEqual(self._remote_label(ProjectCompanyLink, "project"), "project.Project")
        self.assertEqual(self._remote_label(ProjectZip, "project"), "project.Project")
        self.assertEqual(self._remote_label(ProtectedProjectDocument, "project"), "project.Project")

    def test_client_fks_point_to_configured_client_company_model(self):
        self.assertEqual(self._remote_label(ProjectCompanyLink, "client"), "users.ClientCompany")
        self.assertEqual(self._remote_label(ProtectedClientDocument, "client"), "users.ClientCompany")

    def test_user_fks_point_to_auth_user_model(self):
        self.assertEqual(self._remote_label(ProjectZip, "user"), settings.AUTH_USER_MODEL)
        self.assertEqual(self._remote_label(ProtectedProjectDocument, "user"), settings.AUTH_USER_MODEL)
        self.assertEqual(self._remote_label(ProtectedClientDocument, "user"), settings.AUTH_USER_MODEL)


class UserTypeChoicesTests(TestCase):
    def test_user_type_field_uses_frozen_choices(self):
        for model in (ProtectedProjectDocument, ProtectedClientDocument):
            self.assertEqual(model._meta.get_field("user_type").choices, USER_TYPE_CHOICES)

    def test_frozen_choices_match_host_user_model(self):
        """Drift guard: the frozen copy must stay identical to users.User.TYPE_CHOICES."""
        host_choices = get_user_model().TYPE_CHOICES
        self.assertEqual(
            [(value, str(label)) for value, label in USER_TYPE_CHOICES],
            [(value, str(label)) for value, label in host_choices],
        )


class UrlSettingsTests(SimpleTestCase):
    """A9: host URL names resolved via settings instead of hardcoded names."""

    def test_project_detail_url_defaults_to_project_detail(self):
        self.assertEqual(conf.get_project_detail_url(), "project:detail")

    @override_settings(DATA_ROOM_PROJECT_DETAIL_URL="custom:detail")
    def test_project_detail_url_respects_override(self):
        self.assertEqual(conf.get_project_detail_url(), "custom:detail")

    def test_login_url_resolves_settings_login_url(self):
        from django.shortcuts import resolve_url

        self.assertEqual(conf.get_login_url(), resolve_url(settings.LOGIN_URL))

    @override_settings(LOGIN_URL="/custom-login/")
    def test_login_url_honors_login_url_override(self):
        self.assertEqual(conf.get_login_url(), "/custom-login/")
