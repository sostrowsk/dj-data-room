"""Tests for ClientMatcherService."""

import pytest

from data_room.services.client_matcher import ClientMatcherService
from users.models import ClientCompany


@pytest.fixture
def holding_parent(db):
    """Create a holding parent company (holding is self-referential FK)."""
    return ClientCompany.objects.create(
        company="Holding Parent GmbH",
        register_number="HRB 00001",
        is_active=True,
        holding=None,
    )


@pytest.fixture
def active_client(db, holding_parent):
    """Create an active client company."""
    return ClientCompany.objects.create(
        company="Active Company GmbH",
        register_number="HRB 12345",
        is_active=True,
        holding=holding_parent,
    )


@pytest.fixture
def inactive_client(db, holding_parent):
    """Create an inactive client company."""
    return ClientCompany.objects.create(
        company="Inactive Company GmbH",
        register_number="HRB 99999",
        is_active=False,
        holding=holding_parent,
    )


@pytest.fixture
def another_active_client(db, holding_parent):
    """Create another active client company in the same holding."""
    return ClientCompany.objects.create(
        company="Another Active GmbH",
        register_number="HRB 54321",
        is_active=True,
        holding=holding_parent,
    )


class TestInactiveCompanyFiltering:
    """Tests for inactive company exclusion."""

    def test_inactive_companies_excluded_from_hrb_search(self, db, inactive_client):
        """Inactive ClientCompany not returned by HRB search without scoped client."""
        matcher = ClientMatcherService(client_company=None, use_llm=False)
        result = matcher._find_by_registration_number("HRB 99999")
        assert result is None

    def test_inactive_companies_excluded_from_name_search(self, db, inactive_client):
        """Inactive ClientCompany not returned by name search without scoped client."""
        matcher = ClientMatcherService(client_company=None, use_llm=False)
        candidates = matcher._find_name_candidates("Inactive Company")
        assert len(candidates) == 0

    def test_active_companies_found_in_hrb_search(self, db, active_client):
        """Active ClientCompany returned by HRB search."""
        matcher = ClientMatcherService(client_company=None, use_llm=False)
        result = matcher._find_by_registration_number("HRB 12345")
        assert result is not None
        assert result.id == active_client.id

    def test_active_companies_found_in_name_search(self, db, active_client):
        """Active ClientCompany returned by name search."""
        matcher = ClientMatcherService(client_company=None, use_llm=False)
        candidates = matcher._find_name_candidates("Active Company")
        assert len(candidates) == 1
        assert candidates[0].id == active_client.id


class TestScopedClientCompanyBehavior:
    """Tests for scoped client_company behavior."""

    def test_scoped_client_company_included_even_if_inactive_hrb(self, db, inactive_client):
        """Explicitly provided client_company is returned even if inactive (HRB search)."""
        matcher = ClientMatcherService(client_company=inactive_client, use_llm=False)
        result = matcher._find_by_registration_number("HRB 99999")
        assert result is not None
        assert result.id == inactive_client.id

    def test_scoped_client_company_included_even_if_inactive_name(self, db, inactive_client):
        """Explicitly provided client_company is returned even if inactive (name search)."""
        matcher = ClientMatcherService(client_company=inactive_client, use_llm=False)
        candidates = matcher._find_name_candidates("Inactive Company")
        assert len(candidates) == 1
        assert candidates[0].id == inactive_client.id

    def test_holding_peers_must_be_active(self, db, inactive_client, another_active_client, holding_parent):
        """When scoped to holding, only active peers are returned (plus the scoped company)."""
        # inactive_client and another_active_client share the same holding
        matcher = ClientMatcherService(client_company=inactive_client, use_llm=False)

        # Search for the active peer by HRB
        result = matcher._find_by_registration_number("HRB 54321")
        assert result is not None
        assert result.id == another_active_client.id

    def test_scoped_client_without_holding_searches_all_active(self, db):
        """Scoped client without holding searches all active companies."""
        standalone = ClientCompany.objects.create(
            company="Standalone GmbH",
            register_number="HRB 11111",
            is_active=True,
            holding=None,
        )
        other = ClientCompany.objects.create(
            company="Other Company GmbH",
            register_number="HRB 22222",
            is_active=True,
            holding=None,
        )

        matcher = ClientMatcherService(client_company=standalone, use_llm=False)

        result = matcher._find_by_registration_number("HRB 11111")
        assert result is not None
        assert result.id == standalone.id

        # No holding — should still find other active companies
        result = matcher._find_by_registration_number("HRB 22222")
        assert result is not None
        assert result.id == other.id

    def test_scoped_client_without_holding_finds_name_candidates(self, db):
        """Scoped client without holding finds other active companies by name."""
        standalone = ClientCompany.objects.create(
            company="Standalone GmbH",
            register_number="HRB 11111",
            is_active=True,
            holding=None,
        )
        ClientCompany.objects.create(
            company="Other Company GmbH",
            register_number="HRB 22222",
            is_active=True,
            holding=None,
        )

        matcher = ClientMatcherService(client_company=standalone, use_llm=False)
        candidates = matcher._find_name_candidates("Other Company")
        assert len(candidates) == 1
        assert candidates[0].company == "Other Company GmbH"

    def test_inactive_standalone_still_found_when_scoped_hrb(self, db):
        """Inactive standalone client is found by HRB when explicitly scoped."""
        inactive_standalone = ClientCompany.objects.create(
            company="Dormant GmbH",
            register_number="HRB 33333",
            is_active=False,
            holding=None,
        )

        matcher = ClientMatcherService(client_company=inactive_standalone, use_llm=False)
        result = matcher._find_by_registration_number("HRB 33333")
        assert result is not None
        assert result.id == inactive_standalone.id

    def test_inactive_standalone_still_found_when_scoped_name(self, db):
        """Inactive standalone client is found by name when explicitly scoped."""
        inactive_standalone = ClientCompany.objects.create(
            company="Dormant GmbH",
            register_number="HRB 33333",
            is_active=False,
            holding=None,
        )

        matcher = ClientMatcherService(client_company=inactive_standalone, use_llm=False)
        candidates = matcher._find_name_candidates("Dormant")
        assert len(candidates) == 1
        assert candidates[0].id == inactive_standalone.id


class TestHrbNormalization:
    """Tests for HRB normalization."""

    def test_normalize_hrb_adds_space(self, db, active_client):
        """HRB without space is normalized to have space."""
        matcher = ClientMatcherService(client_company=None, use_llm=False)

        # active_client has "HRB 12345", search with "HRB12345"
        result = matcher._find_by_registration_number("HRB12345")
        assert result is not None
        assert result.id == active_client.id

    def test_normalize_hrb_case_insensitive(self, db, active_client):
        """HRB search is case insensitive."""
        matcher = ClientMatcherService(client_company=None, use_llm=False)

        result = matcher._find_by_registration_number("hrb 12345")
        assert result is not None
        assert result.id == active_client.id
