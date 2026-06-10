"""
Form for confirming extracted client entities.

Allows users to:
- Select an existing client match
- Create a new client from extracted data
- Skip an entity
"""

from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from data_room.conf import get_client_company_model
from data_room.helpers.find_similar_client import find_similar_client
from data_room.services.company_names import company_name_similarity

ClientCompany = get_client_company_model()


class ClientConfirmationForm(forms.Form):
    """Form for a single extracted client entity confirmation."""

    # Hidden field to store the original extracted data
    entity_index = forms.IntegerField(widget=forms.HiddenInput())

    # Action choice
    ACTION_CHOICES = [
        ("use_existing", _("Use existing client")),
        ("create_new", _("Create new client")),
        ("skip", _("Skip this entity")),
    ]
    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        initial="use_existing",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    # For selecting existing client
    existing_client = forms.ModelChoiceField(
        queryset=ClientCompany.objects.none(),
        required=False,
        empty_label=_("-- Select existing client --"),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
        label=_("Existing Client"),
    )

    # For creating new client
    new_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    new_registration_number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "HRB 12345"}),
    )
    new_legal_form = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "GmbH, SE, AG..."}),
    )
    new_parent = forms.ModelChoiceField(
        queryset=ClientCompany.objects.none(),
        required=False,
        empty_label=_("(No parent - create as holding)"),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
        label=_("Parent Company"),
    )

    def __init__(self, *args, client_company=None, extracted_name=None, extracted_hrb=None, **kwargs):
        super().__init__(*args, **kwargs)

        if client_company:
            # Get group companies (full corporate group)
            group = client_company.get_group()
            group_ids = {c.pk for c in group}

            qs = ClientCompany.objects.filter(pk__in=group_ids, is_active=True) | ClientCompany.objects.filter(
                pk=client_company.pk
            )

            # Also include any active company matching the document's extracted data
            if extracted_name or extracted_hrb:
                match_q = Q()
                if extracted_name:
                    match_q |= Q(company=extracted_name, is_active=True)
                if extracted_hrb:
                    match_q |= Q(register_number=extracted_hrb, is_active=True)
                qs = qs | ClientCompany.objects.filter(match_q)
        else:
            qs = ClientCompany.objects.filter(is_active=True)

        qs = qs.distinct().order_by("company")
        self.fields["existing_client"].queryset = qs
        self.fields["existing_client"].label_from_instance = lambda obj: f"{obj.company} ({obj.register_number or '-'})"
        self.fields["new_parent"].queryset = qs

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get("action")

        # Clear errors on fields irrelevant to the selected action
        if action in ("use_existing", "skip"):
            for field in ("new_name", "new_registration_number", "new_legal_form", "new_parent"):
                self.errors.pop(field, None)
        if action in ("create_new", "skip"):
            self.errors.pop("existing_client", None)

        if action == "use_existing":
            if not cleaned_data.get("existing_client"):
                raise forms.ValidationError(_("Please select an existing client"))

        elif action == "create_new":
            new_name = cleaned_data.get("new_name")
            if not new_name:
                raise forms.ValidationError(_("Please enter a name for the new client"))
            existing = ClientCompany.objects.filter(company__iexact=new_name).first()
            if existing:
                raise forms.ValidationError(
                    _("Company '%(name)s' already exists. Please use 'Use existing client' instead."),
                    params={"name": new_name},
                )
            else:
                result = find_similar_client(new_name)
                if result:
                    similar, score = result
                    self.add_error(
                        "new_name",
                        _(
                            'Similar client found: "%(name)s" (ID %(id)s). '
                            "Please check or select the existing client."
                        )
                        % {"name": similar.company, "id": similar.pk},
                    )

        return cleaned_data


class ClientConfirmationFormSet:
    """Helper class to manage multiple ClientConfirmationForms."""

    def __init__(self, extracted_data, client_company=None, data=None):
        """
        Initialize the formset.

        Args:
            extracted_data: List of extracted client data dicts from document
            client_company: Optional ClientCompany to scope client selection
            data: POST data if submitting
        """
        self.extracted_data = extracted_data
        self.client_company = client_company
        self.forms = []

        for i, entity in enumerate(extracted_data):
            prefix = f"entity_{i}"
            initial = {
                "entity_index": i,
                "new_name": entity.get("name", ""),
                "new_registration_number": entity.get("registration_number", ""),
                "new_legal_form": entity.get("legal_form", ""),
            }

            # Set action based on HRB presence and match quality
            match_info = entity.get("match", {})
            match_type = match_info.get("match_type", "none")
            has_hrb = bool(entity.get("registration_number"))
            has_match = bool(match_info.get("existing_client_id"))
            is_strong_match = match_type in ("exact_hrb", "exact_name")

            if has_hrb and has_match and is_strong_match:
                # HRB found AND strong match (exact HRB or exact name) -> use existing
                initial["action"] = "use_existing"
                initial["existing_client"] = match_info.get("existing_client_id")
                entity["suggested_action"] = "use_existing"
            elif has_hrb and not has_match:
                # HRB found but no match -> create new
                initial["action"] = "create_new"
                entity["suggested_action"] = "create_new"
            else:
                # No HRB, or weak match (suggested/llm_confirmed) -> skip
                initial["action"] = "skip"
                if has_match:
                    initial["existing_client"] = match_info.get("existing_client_id")
                entity["suggested_action"] = None

            form = ClientConfirmationForm(
                data=data,
                prefix=prefix,
                initial=initial,
                client_company=client_company,
                extracted_name=entity.get("name"),
                extracted_hrb=entity.get("registration_number"),
            )

            # Fallback: if action is "create_new" but the company already
            # exists in the queryset, switch to "use_existing"
            if initial["action"] == "create_new" and initial.get("new_name"):
                exact = form.fields["existing_client"].queryset.filter(company__iexact=initial["new_name"]).first()
                if exact:
                    form.initial["action"] = "use_existing"
                    form.initial["existing_client"] = exact.pk
                    entity["suggested_action"] = "use_existing"
                    entity.setdefault("match", {}).update(
                        {
                            "existing_client_id": exact.pk,
                            "existing_client_name": exact.company,
                            "match_type": "exact_name",
                            "match_confidence": 1.0,
                        }
                    )

            # Fuzzy-match fallback: if no client pre-selected, try similarity
            if not form.initial.get("existing_client") and initial.get("new_name"):
                best_client, best_score = None, 0.0
                for client in form.fields["existing_client"].queryset:
                    score = company_name_similarity(initial["new_name"], client.company)
                    if score >= 0.85 and score > best_score:
                        best_client, best_score = client, score
                if best_client:
                    form.initial["action"] = "use_existing"
                    form.initial["existing_client"] = best_client.pk
                    entity["suggested_action"] = "use_existing"
                    # Update match info so the template badge reflects the fuzzy match
                    entity.setdefault("match", {}).update(
                        {
                            "existing_client_id": best_client.pk,
                            "existing_client_name": best_client.company,
                            "match_type": "suggested",
                            "match_confidence": best_score,
                        }
                    )

            # Attach entity data for template rendering
            form.entity_data = entity
            self.forms.append(form)

        # Enforce single non-skip suggestion: first wins, rest default to skip
        non_skip = [(i, f) for i, f in enumerate(self.forms) if f.initial.get("action") != "skip"]
        if len(non_skip) > 1:
            for i, form in non_skip[1:]:
                form.initial["action"] = "skip"
                self.extracted_data[i]["suggested_action"] = None

    def is_valid(self):
        """Check if all forms are valid."""
        return all(form.is_valid() for form in self.forms)

    def __iter__(self):
        return iter(self.forms)

    def __len__(self):
        return len(self.forms)
