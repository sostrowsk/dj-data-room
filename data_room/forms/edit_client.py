from django import forms
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from data_room.conf import get_client_company_model
from data_room.helpers.find_similar_client import find_similar_client
from data_room.models.choices import ClientDocumentType, StatementType

ClientCompany = get_client_company_model()


class EditLinkedClientForm(forms.Form):
    """Form for editing the client of a document or creating a new client."""

    ACTION_CHOICES = [
        ("use_existing", _("Use existing client")),
        ("create_new", _("Create new client")),
    ]

    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        initial="use_existing",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )

    document_type = forms.ChoiceField(
        choices=[("", "---------")] + list(ClientDocumentType.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Document Type"),
    )

    statement_type = forms.ChoiceField(
        choices=StatementType.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Statement Type"),
    )

    # For selecting existing client
    linked_client = forms.ModelChoiceField(
        queryset=ClientCompany.objects.none(),
        required=False,
        empty_label="(Kein Mandant verknüpft)",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # Fields for creating new client
    new_company_name = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    new_register_number = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    # Parent selector for new client
    new_parent = forms.ModelChoiceField(
        queryset=ClientCompany.objects.none(),
        required=False,
        empty_label=_("(No parent - create as holding)"),
        widget=forms.Select(attrs={"class": "form-select"}),
        label=_("Parent Company"),
    )

    def __init__(self, *args, client_company=None, extracted_name=None, extracted_hrb=None, **kwargs):
        super().__init__(*args, **kwargs)

        if client_company:
            # Get group companies (full corporate group)
            group = client_company.get_group()
            group_ids = {c.pk for c in group}

            qs = ClientCompany.objects.filter(pk__in=group_ids)

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
        self.fields["linked_client"].queryset = qs
        self.fields["new_parent"].queryset = qs

    def clean(self):
        cleaned_data = super().clean()
        action = cleaned_data.get("action")

        if action == "create_new":
            company_name = cleaned_data.get("new_company_name")
            if not company_name:
                self.add_error("new_company_name", _("Company name is required."))
            elif ClientCompany.objects.filter(company=company_name).exists():
                self.add_error(
                    "new_company_name",
                    _("A company with this name already exists."),
                )
            else:
                result = find_similar_client(company_name)
                if result:
                    similar, score = result
                    self.add_error(
                        "new_company_name",
                        _(
                            'Similar client found: "%(name)s" (ID %(id)s). '
                            "Please check or select the existing client."
                        )
                        % {"name": similar.company, "id": similar.pk},
                    )

        return cleaned_data
