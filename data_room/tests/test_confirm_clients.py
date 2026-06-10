"""Tests for client confirmation form and views."""

import pytest
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse

from data_room.forms.confirm_clients import ClientConfirmationForm, ClientConfirmationFormSet
from data_room.models import ProtectedClientDocument
from users.factories import create_broker, create_client, login_and_verify
from users.models import ClientCompany


@pytest.fixture
def holding_parent(db):
    """Create a holding parent company."""
    return ClientCompany.objects.create(
        company="Holding Parent GmbH",
        register_number="HRB 00001",
        is_active=True,
        holding=None,
    )


@pytest.fixture
def active_client_company(db, holding_parent):
    """Create an active client company."""
    return ClientCompany.objects.create(
        company="Active Company GmbH",
        register_number="HRB 12345",
        is_active=True,
        holding=holding_parent,
    )


@pytest.fixture
def inactive_client_company(db, holding_parent):
    """Create an inactive client company."""
    return ClientCompany.objects.create(
        company="Inactive Company GmbH",
        register_number="HRB 99999",
        is_active=False,
        holding=holding_parent,
    )


@pytest.fixture
def standalone_client_company(db):
    """Create a standalone client company without holding."""
    return ClientCompany.objects.create(
        company="Standalone GmbH",
        register_number="HRB 55555",
        is_active=True,
        holding=None,
    )


@pytest.fixture
def broker_user(db):
    """Create a broker user."""
    return create_broker()


@pytest.fixture
def client_user(db):
    """Create a client user."""
    broker = create_broker()
    return create_client(broker=broker)


@pytest.fixture
def protected_client_document(db, active_client_company, broker_user):
    """Create a ProtectedClientDocument for testing."""
    doc = ProtectedClientDocument(
        client=active_client_company,
        name="Test Document.pdf",
        user=broker_user,
        user_type="broker",
        user_company=broker_user.broker_company.company,
        indexing_status="indexed",
        client_extraction_status="awaiting_confirmation",
        extracted_clients_data=[
            {
                "name": "Test GmbH",
                "registration_number": "HRB 12345",
                "legal_form": "GmbH",
                "role": "borrower",
                "confidence": 0.95,
            }
        ],
    )
    doc.file.save("test.pdf", ContentFile(b"%PDF-1.4 test"), save=False)
    doc.save(skip_preview=True)
    return doc


class TestClientConfirmationForm:
    """Tests for ClientConfirmationForm."""

    def test_queryset_filters_inactive_companies_without_scope(
        self, db, active_client_company, inactive_client_company
    ):
        """Without client_company scope, only active companies appear."""
        form = ClientConfirmationForm()
        qs = form.fields["existing_client"].queryset

        assert active_client_company in qs
        assert inactive_client_company not in qs

    def test_scoped_client_included_even_if_inactive(self, db, inactive_client_company):
        """Scoped client_company is included even if inactive."""
        form = ClientConfirmationForm(client_company=inactive_client_company)
        qs = form.fields["existing_client"].queryset

        assert inactive_client_company in qs

    def test_holding_siblings_active_only(self, db, holding_parent, active_client_company, inactive_client_company):
        """When scoped to holding, only active siblings appear (plus scoped company)."""
        form = ClientConfirmationForm(client_company=active_client_company)
        qs = form.fields["existing_client"].queryset

        assert active_client_company in qs
        assert inactive_client_company not in qs
        # Note: holding_parent may or may not be in qs depending on whether it's in the same holding group

    def test_standalone_client_only_sees_itself(self, db, standalone_client_company, active_client_company):
        """Standalone client without holding only sees itself."""
        form = ClientConfirmationForm(client_company=standalone_client_company)
        qs = form.fields["existing_client"].queryset

        assert standalone_client_company in qs
        assert active_client_company not in qs

    def test_label_includes_register_number(self, db, active_client_company):
        """Label shows company name and register number."""
        form = ClientConfirmationForm(client_company=active_client_company)
        label_func = form.fields["existing_client"].label_from_instance

        label = label_func(active_client_company)
        assert "Active Company GmbH" in label
        assert "HRB 12345" in label

    @override_settings(LANGUAGE_CODE="en")
    def test_clean_requires_existing_client_for_use_existing(self, db, active_client_company):
        """Validation fails if use_existing selected without client."""
        form = ClientConfirmationForm(
            client_company=active_client_company,
            data={
                "entity_index": 0,
                "action": "use_existing",
                "existing_client": "",
            },
        )
        assert not form.is_valid()
        assert "Please select an existing client" in str(form.errors)

    @override_settings(LANGUAGE_CODE="en")
    def test_clean_requires_name_for_create_new(self, db, active_client_company):
        """Validation fails if create_new selected without name."""
        form = ClientConfirmationForm(
            client_company=active_client_company,
            data={
                "entity_index": 0,
                "action": "create_new",
                "new_name": "",
            },
        )
        assert not form.is_valid()
        assert "Please enter a name" in str(form.errors)

    def test_skip_action_requires_no_additional_fields(self, db, active_client_company):
        """Skip action is valid without additional fields."""
        form = ClientConfirmationForm(
            client_company=active_client_company,
            data={
                "entity_index": 0,
                "action": "skip",
            },
        )
        assert form.is_valid()

    @override_settings(LANGUAGE_CODE="en")
    def test_create_new_rejects_case_insensitive_duplicate(self, db, active_client_company):
        """create_new with a name differing only in case is rejected."""
        form = ClientConfirmationForm(
            client_company=active_client_company,
            data={
                "entity_index": 0,
                "action": "create_new",
                "new_name": active_client_company.company.lower(),
            },
        )
        assert not form.is_valid()
        assert "already exists" in str(form.non_field_errors())

    @override_settings(LANGUAGE_CODE="en")
    def test_create_new_raises_error_for_existing_company(self, db, active_client_company):
        """create_new with a name that already exists raises a non-field ValidationError."""
        form = ClientConfirmationForm(
            client_company=active_client_company,
            data={
                "entity_index": 0,
                "action": "create_new",
                "new_name": active_client_company.company,
            },
        )
        assert not form.is_valid()
        assert "already exists" in str(form.non_field_errors())
        # Action should NOT be silently switched
        assert form.cleaned_data.get("action") != "use_existing"


class TestClientConfirmationFormSet:
    """Tests for ClientConfirmationFormSet."""

    def test_formset_creates_forms_for_each_entity(self, db, active_client_company):
        """Formset creates one form per extracted entity."""
        extracted_data = [
            {"name": "Company A", "registration_number": "HRB 111"},
            {"name": "Company B", "registration_number": "HRB 222"},
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        assert len(formset) == 2
        assert formset.forms[0].entity_data["name"] == "Company A"
        assert formset.forms[1].entity_data["name"] == "Company B"

    def test_formset_preselects_strong_match(self, db, active_client_company):
        """Strong match (exact_hrb) preselects use_existing."""
        extracted_data = [
            {
                "name": "Test",
                "registration_number": "HRB 12345",
                "match": {
                    "existing_client_id": active_client_company.id,
                    "match_type": "exact_hrb",
                },
            }
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        assert formset.forms[0].initial["action"] == "use_existing"
        assert formset.forms[0].initial["existing_client"] == active_client_company.id

    def test_formset_preselects_create_for_unmatched_hrb(self, db, active_client_company):
        """Entity with HRB but no match preselects create_new."""
        extracted_data = [
            {
                "name": "New Company",
                "registration_number": "HRB 99999",
                "match": {},  # No match
            }
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        assert formset.forms[0].initial["action"] == "create_new"

    def test_formset_preselects_skip_for_weak_match(self, db, active_client_company):
        """Weak match (suggested) defaults to skip."""
        extracted_data = [
            {
                "name": "Maybe Match",
                "match": {
                    "existing_client_id": active_client_company.id,
                    "match_type": "suggested",
                },
            }
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        assert formset.forms[0].initial["action"] == "skip"

    def test_formset_fuzzy_matches_existing_client(self, db, active_client_company):
        """Fuzzy match pre-selects use_existing when entity name closely matches a dropdown entry."""
        extracted_data = [
            {
                "name": "active company gmbh",  # lowercase variant
                "match": {},  # no match info
            }
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        form = formset.forms[0]
        assert form.initial["action"] == "use_existing"
        assert form.initial["existing_client"] == active_client_company.pk

    def test_formset_only_one_non_skip_suggestion(self, db, active_client_company, holding_parent):
        """When multiple entities have strong matches, only the first gets non-skip."""
        second_company = ClientCompany.objects.create(
            company="Second Company GmbH",
            register_number="HRB 00001",
            is_active=True,
            holding=holding_parent,
        )
        extracted_data = [
            {
                "name": "Active Company GmbH",
                "registration_number": "HRB 12345",
                "match": {
                    "existing_client_id": active_client_company.id,
                    "match_type": "exact_hrb",
                },
            },
            {
                "name": "Second Company GmbH",
                "registration_number": "HRB 00001",
                "match": {
                    "existing_client_id": second_company.id,
                    "match_type": "exact_hrb",
                },
            },
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        # First entity keeps its suggestion
        assert formset.forms[0].initial["action"] == "use_existing"
        assert formset.forms[0].initial["existing_client"] == active_client_company.id

        # Second entity reset to skip
        assert formset.forms[1].initial["action"] == "skip"
        assert extracted_data[1]["suggested_action"] is None

        # But existing_client is still pre-filled (dropdown ready)
        assert formset.forms[1].initial["existing_client"] == second_company.id

    def test_formset_fallback_switches_create_to_use_existing(self, db, active_client_company):
        """Formset switches create_new to use_existing when company already exists in queryset."""
        extracted_data = [
            {
                "name": active_client_company.company,
                "registration_number": "HRB 99999",  # HRB present but no match -> would be create_new
                "match": {},
            }
        ]
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=active_client_company,
        )

        form = formset.forms[0]
        assert form.initial["action"] == "use_existing"
        assert form.initial["existing_client"] == active_client_company.pk


@pytest.mark.django_db
class TestConfirmClientsClientDocView:
    """Tests for hx_confirm_clients_client_doc view."""

    def test_get_renders_modal(self, client, broker_user, protected_client_document):
        """GET request renders the confirmation modal."""
        login_and_verify(broker_user, client)

        url = reverse("data_room:hx-confirm-clients-client-doc", args=[protected_client_document.pk])
        response = client.get(url)

        assert response.status_code == 200
        assert b"Test GmbH" in response.content

    def test_permission_denied_for_client_user(self, client, client_user, protected_client_document):
        """Client users cannot access the confirmation view."""
        login_and_verify(client_user, client)

        url = reverse("data_room:hx-confirm-clients-client-doc", args=[protected_client_document.pk])
        response = client.get(url)

        assert response.status_code == 403

    def test_post_use_existing_links_client(
        self, client, broker_user, protected_client_document, active_client_company
    ):
        """POST with use_existing links the client to the document."""
        login_and_verify(broker_user, client)

        url = reverse("data_room:hx-confirm-clients-client-doc", args=[protected_client_document.pk])
        response = client.post(
            url,
            {
                "entity_0-entity_index": 0,
                "entity_0-action": "use_existing",
                "entity_0-existing_client": active_client_company.pk,
            },
        )

        assert response.status_code == 200
        protected_client_document.refresh_from_db()
        assert protected_client_document.client == active_client_company
        assert protected_client_document.client_extraction_status == "completed"

    def test_post_create_new_creates_and_links(self, client, broker_user, protected_client_document):
        """POST with create_new creates a new client and links it."""
        login_and_verify(broker_user, client)

        url = reverse("data_room:hx-confirm-clients-client-doc", args=[protected_client_document.pk])
        response = client.post(
            url,
            {
                "entity_0-entity_index": 0,
                "entity_0-action": "create_new",
                "entity_0-new_name": "Brand New GmbH",
                "entity_0-new_registration_number": "HRB 77777",
                "entity_0-new_legal_form": "GmbH",
            },
        )

        assert response.status_code == 200
        protected_client_document.refresh_from_db()
        assert protected_client_document.client.company == "Brand New GmbH"
        assert protected_client_document.client.register_number == "HRB 77777"

    def test_post_skip_does_not_link(self, client, broker_user, protected_client_document, active_client_company):
        """POST with skip action keeps the original client."""
        login_and_verify(broker_user, client)

        url = reverse("data_room:hx-confirm-clients-client-doc", args=[protected_client_document.pk])
        response = client.post(
            url,
            {
                "entity_0-entity_index": 0,
                "entity_0-action": "skip",
            },
        )

        assert response.status_code == 200
        protected_client_document.refresh_from_db()
        assert protected_client_document.client == active_client_company
        assert protected_client_document.client_extraction_status == "completed"

    def test_use_existing_updates_missing_hrb(
        self, client, broker_user, protected_client_document, active_client_company
    ):
        """Using existing client updates its HRB if it was missing."""
        # Remove HRB from client
        active_client_company.register_number = ""
        active_client_company.save()

        # Update document's extracted data to have HRB
        protected_client_document.extracted_clients_data = [{"name": "Test", "registration_number": "HRB 88888"}]
        protected_client_document.save()

        login_and_verify(broker_user, client)

        url = reverse("data_room:hx-confirm-clients-client-doc", args=[protected_client_document.pk])
        client.post(
            url,
            {
                "entity_0-entity_index": 0,
                "entity_0-action": "use_existing",
                "entity_0-existing_client": active_client_company.pk,
            },
        )

        active_client_company.refresh_from_db()
        assert active_client_company.register_number == "HRB 88888"


@pytest.mark.django_db
class TestConfirmClientsProjectDocView:
    """Tests for hx_confirm_clients view (project documents).

    Note: ProtectedProjectDocument no longer has client_extraction_status,
    extracted_clients_data, or linked_client fields (removed in migration 0017).
    Client extraction is only available for ProtectedClientDocument.
    """

    pass  # Tests removed - feature no longer exists for project documents
