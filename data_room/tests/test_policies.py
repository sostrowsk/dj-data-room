"""Tests for the data_room permission-policy framework (plan step A4).

Scope per plan: ONLY the ``get_policy()`` dispatch and the generic
``DefaultPolicy``. The leasing-specific ``LeasingDataRoomPolicy`` is
covered by the existing (untouched) permission test suite = parity proof.

DefaultPolicy is host-model-agnostic (duck-typed), so plain stubs are
used instead of DB-backed factories — no database access required.
"""

from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils.module_loading import import_string

import data_room.policies as policies
from data_room.policies import BaseDataRoomPolicy, DefaultPolicy, get_policy


def _user(authenticated=True, staff=False, superuser=False, pk=1):
    return SimpleNamespace(
        is_authenticated=authenticated,
        is_staff=staff,
        is_superuser=superuser,
        pk=pk,
    )


def _document(user_id=None):
    return SimpleNamespace(user_id=user_id)


class GetPolicyDispatchTests(SimpleTestCase):
    def setUp(self):
        get_policy.cache_clear()
        self.addCleanup(get_policy.cache_clear)

    def test_get_policy_resolves_configured_setting(self):
        """get_policy() instantiates the class named by DATA_ROOM_PERMISSION_POLICY."""
        configured = import_string(settings.DATA_ROOM_PERMISSION_POLICY)
        self.assertIsInstance(get_policy(), configured)

    @override_settings(DATA_ROOM_PERMISSION_POLICY="data_room.policies.DefaultPolicy")
    def test_get_policy_dispatches_to_overridden_setting(self):
        self.assertIs(type(get_policy()), DefaultPolicy)

    def test_get_policy_defaults_to_default_policy_when_setting_absent(self):
        with mock.patch.object(policies, "settings", SimpleNamespace()):
            self.assertIs(type(get_policy()), DefaultPolicy)

    @override_settings(DATA_ROOM_PERMISSION_POLICY="data_room.policies.DefaultPolicy")
    def test_get_policy_caches_the_instance(self):
        self.assertIs(get_policy(), get_policy())


class DefaultPolicyDocumentGateTests(SimpleTestCase):
    """staff/superuser may do everything; the author may view + manage."""

    DOCUMENT_GATES = (
        "can_view_project_document",
        "can_view_client_document",
        "can_manage_project_document",
        "can_manage_client_document",
        "can_download_original",
        "can_curate_clients",
    )

    def setUp(self):
        self.policy = DefaultPolicy()

    def test_default_policy_implements_the_base_contract(self):
        self.assertIsInstance(self.policy, BaseDataRoomPolicy)

    def test_staff_and_superuser_pass_every_document_gate(self):
        for gate in self.DOCUMENT_GATES:
            with self.subTest(gate=gate):
                self.assertTrue(getattr(self.policy, gate)(_user(staff=True), _document()))
                self.assertTrue(getattr(self.policy, gate)(_user(superuser=True), _document()))

    def test_author_passes_every_document_gate_for_own_document(self):
        user = _user(pk=7)
        for gate in self.DOCUMENT_GATES:
            with self.subTest(gate=gate):
                self.assertTrue(getattr(self.policy, gate)(user, _document(user_id=7)))

    def test_non_author_is_denied_on_every_document_gate(self):
        user = _user(pk=7)
        for gate in self.DOCUMENT_GATES:
            with self.subTest(gate=gate):
                self.assertFalse(getattr(self.policy, gate)(user, _document(user_id=8)))
                self.assertFalse(getattr(self.policy, gate)(user, _document(user_id=None)))

    def test_anonymous_user_is_denied_on_every_document_gate(self):
        anonymous = _user(authenticated=False, pk=None)
        for gate in self.DOCUMENT_GATES:
            with self.subTest(gate=gate):
                self.assertFalse(getattr(self.policy, gate)(anonymous, _document(user_id=7)))


class DefaultPolicyProjectAndScopeTests(SimpleTestCase):
    def setUp(self):
        self.policy = DefaultPolicy()

    def test_can_access_project_delegates_to_project_check_permissions(self):
        user = _user()
        project = mock.Mock(spec=["check_permissions"])
        project.check_permissions.return_value = True
        self.assertTrue(self.policy.can_access_project(user, project))
        project.check_permissions.assert_called_once_with(user)
        project.check_permissions.return_value = False
        self.assertFalse(self.policy.can_access_project(user, project))

    def test_can_access_project_without_check_permissions_is_staff_only(self):
        project = SimpleNamespace()
        self.assertTrue(self.policy.can_access_project(_user(staff=True), project))
        self.assertFalse(self.policy.can_access_project(_user(), project))

    def test_can_manage_project_documents_is_staff_only(self):
        project = SimpleNamespace()
        self.assertTrue(self.policy.can_manage_project_documents(_user(staff=True), project))
        self.assertFalse(self.policy.can_manage_project_documents(_user(), project))

    def test_can_view_company_documents_is_staff_only(self):
        company = SimpleNamespace()
        self.assertTrue(self.policy.can_view_company_documents(_user(superuser=True), company))
        self.assertFalse(self.policy.can_view_company_documents(_user(), company))

    def test_filter_project_document_buckets_returns_buckets_unchanged(self):
        buckets = {"client": object(), "broker": object(), "partner": object()}
        result = self.policy.filter_project_document_buckets(_user(), SimpleNamespace(), buckets)
        self.assertIs(result, buckets)

    def test_get_author_scope_keeps_everything_for_staff(self):
        documents = mock.Mock(spec=["filter"])
        self.assertIs(self.policy.get_author_scope(_user(staff=True), documents), documents)
        documents.filter.assert_not_called()

    def test_get_author_scope_narrows_to_own_documents_for_non_staff(self):
        user = _user(pk=7)
        documents = mock.Mock(spec=["filter"])
        result = self.policy.get_author_scope(user, documents)
        documents.filter.assert_called_once_with(user=user)
        self.assertIs(result, documents.filter.return_value)
