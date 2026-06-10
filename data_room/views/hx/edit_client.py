"""
HTMX view for editing the client of a document.
"""

from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django_otp.decorators import otp_required

from ...conf import get_client_company_model
from ...decorators import project_permission_required
from ...forms import EditLinkedClientForm
from ...models import ProtectedClientDocument
from ...policies import get_policy

ClientCompany = get_client_company_model()


@otp_required
@project_permission_required(htmx_required=True)
def hx_edit_client(request, pk, document_pk):
    """
    HTMX view for editing the client of a project document.

    Note: ProtectedProjectDocument does not have client-specific fields.
    This view exists for URL compatibility but the feature is not functional for project docs.
    """
    return HttpResponse("Not available for project documents", status=400)


@otp_required
def hx_edit_client_client_doc(request, document_pk):
    """
    HTMX view for editing the client of a client document.

    GET: Display edit modal with current client
    POST: Update client or create new client, return OOB update
    """
    user = request.user

    if not get_policy().can_curate_clients(user):
        return HttpResponse("Permission denied", status=403)

    document = get_object_or_404(
        ProtectedClientDocument.objects.select_related("client"),
        pk=document_pk,
    )

    client_company = document.client

    # Get LLM suggestion from extracted_clients_data (first entity)
    suggested_name = ""
    suggested_hrb = ""
    if document.extracted_clients_data:
        first_entity = document.extracted_clients_data[0]
        suggested_name = first_entity.get("name", "")
        suggested_hrb = first_entity.get("registration_number", "")

    if request.method == "POST":
        form = EditLinkedClientForm(
            request.POST,
            client_company=client_company,
            extracted_name=suggested_name,
            extracted_hrb=suggested_hrb,
        )
        if form.is_valid():
            action = form.cleaned_data.get("action", "use_existing")

            if action == "create_new":
                new_parent = form.cleaned_data.get("new_parent")
                try:
                    new_client = ClientCompany.objects.create(
                        company=form.cleaned_data["new_company_name"],
                        register_number=form.cleaned_data.get("new_register_number", ""),
                        holding=new_parent,
                        is_active=True,
                    )
                    document.client = new_client
                except IntegrityError:
                    form.add_error(
                        "new_company_name",
                        _("A company with this name already exists."),
                    )
                    return render(
                        request,
                        "data_room/_hx_edit_client_client_doc.html",
                        {"document": document, "form": form},
                    )
            else:
                document.client = form.cleaned_data["linked_client"]

            document.document_type = form.cleaned_data.get("document_type", "")
            document.statement_type = form.cleaned_data.get("statement_type", "single")
            document.save(update_fields=["client", "document_type", "statement_type"])

            response = render(
                request,
                "data_room/_hx_edit_client_client_doc_oob_update.html",
                {
                    "document": document,
                    "protected_document": document,
                    "user": user,
                },
            )
            response["HX-Refresh"] = "true"
            return response
    else:
        form = EditLinkedClientForm(
            initial={
                "linked_client": document.client,
                "new_company_name": suggested_name,
                "new_register_number": suggested_hrb,
                "document_type": document.document_type,
                "statement_type": document.statement_type,
            },
            client_company=client_company,
            extracted_name=suggested_name,
            extracted_hrb=suggested_hrb,
        )

    return render(
        request,
        "data_room/_hx_edit_client_client_doc.html",
        {
            "document": document,
            "form": form,
        },
    )
