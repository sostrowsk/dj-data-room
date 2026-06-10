"""
HTMX view for confirming extracted client entities.

Allows users to review extracted clients, select existing matches,
create new clients, or skip entities.
"""

import logging

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django_otp.decorators import otp_required

from ...conf import get_client_company_model
from ...forms import ClientConfirmationFormSet
from ...models import ProtectedClientDocument
from ...policies import get_policy

ClientCompany = get_client_company_model()

logger = logging.getLogger(__name__)


def _refresh_matches(extracted_data: list, client_company) -> list:
    """Re-run matching on GET to catch newly created clients."""
    from data_room.schemas import ExtractedClientEntity
    from data_room.services.client_matcher import ClientMatcherService

    entities = [
        ExtractedClientEntity(
            name=item.get("name", ""),
            canonical_name=item.get("canonical_name", ""),
            registration_number=item.get("registration_number"),
            legal_form=item.get("legal_form"),
            role=item.get("role"),
            confidence=item.get("confidence", 0.0),
            additional_identifiers=item.get("additional_identifiers", {}),
        )
        for item in extracted_data
    ]

    matcher = ClientMatcherService(client_company=client_company, use_llm=False)
    match_results = matcher.find_matches(entities)

    for i, match_result in enumerate(match_results):
        if i < len(extracted_data):
            extracted_data[i]["match"] = {
                "existing_client_id": match_result.existing_client_id,
                "existing_client_name": match_result.existing_client_name,
                "match_type": match_result.match_type,
                "match_confidence": match_result.match_confidence,
                "llm_reasoning": match_result.llm_reasoning,
            }
    return extracted_data


def _process_formset(formset, extracted_data: list, client_company=None) -> ClientCompany | None:
    """Process validated formset, return linked client if any."""
    # Server-side enforcement: only one non-skip action allowed
    non_skip_actions = [
        form for form in formset.forms if form.cleaned_data.get("action") in ("use_existing", "create_new")
    ]
    if len(non_skip_actions) > 1:
        for form in non_skip_actions[1:]:
            form.cleaned_data["action"] = "skip"
        logger.info(f"Enforced single selection: skipped {len(non_skip_actions) - 1} extras")

    linked_client = None

    for form in formset.forms:
        action = form.cleaned_data.get("action")
        entity_index = form.cleaned_data.get("entity_index")
        entity_data = extracted_data[entity_index] if entity_index < len(extracted_data) else {}

        if action == "use_existing":
            existing_client = form.cleaned_data.get("existing_client")
            if existing_client:
                linked_client = existing_client
                # Update HRB if extracted and client doesn't have one
                extracted_hrb = entity_data.get("registration_number")
                if extracted_hrb and not existing_client.register_number:
                    existing_client.register_number = extracted_hrb
                    existing_client.save(update_fields=["register_number"])
                    logger.info(f"Updated HRB for client {existing_client.id}")

        elif action == "create_new":
            new_name = form.cleaned_data.get("new_name")
            new_parent = form.cleaned_data.get("new_parent")
            new_client, created = ClientCompany.objects.get_or_create(
                company=new_name,
                defaults={
                    "register_number": form.cleaned_data.get("new_registration_number", ""),
                    "legal_form": form.cleaned_data.get("new_legal_form", ""),
                    "holding": new_parent,
                    "address1": "",
                    "address2": "",
                    "zip_code": "",
                    "city": "",
                },
            )
            linked_client = new_client
            logger.info(f"{'Created' if created else 'Found'} client {new_client.id}")

    return linked_client


@otp_required
def hx_confirm_clients_client_doc(request, document_pk):
    """
    HTMX view for confirming extracted clients for a ProtectedClientDocument.

    GET: Display confirmation form with extracted entities
    POST: Process confirmations, create/link clients
    """
    user = request.user

    # Check permissions
    if not get_policy().can_curate_clients(user):
        return HttpResponse("Permission denied", status=403)

    document = get_object_or_404(
        ProtectedClientDocument.objects.select_related("client"),
        pk=document_pk,
    )

    # Get client_company from document for scoping
    client_company = document.client

    extracted_data = document.extracted_clients_data or []

    # For GET requests: Re-run matching to catch newly created clients
    if request.method == "GET" and extracted_data:
        extracted_data = _refresh_matches(extracted_data, client_company)

    if request.method == "POST":
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=client_company,
            data=request.POST,
        )

        if formset.is_valid():
            linked_client = _process_formset(formset, extracted_data, client_company=client_company)

            # Update client on document
            if linked_client:
                document.client = linked_client

            document.client_extraction_status = "completed"
            document.save(update_fields=["client_extraction_status", "client"])

            # Return OOB swap to update the document row
            response = render(
                request,
                "data_room/_confirm_clients_client_doc_oob_update.html",
                {
                    "document": document,
                    "protected_document": document,
                    "user": user,
                },
            )
            response["HX-Refresh"] = "true"
            return response

    else:
        formset = ClientConfirmationFormSet(
            extracted_data=extracted_data,
            client_company=client_company,
        )

    return render(
        request,
        "data_room/_hx_confirm_clients.html",
        {
            "document": document,
            "formset": formset,
            "extracted_data": extracted_data,
            "is_client_document": True,
        },
    )


@otp_required
def hx_skip_client_extraction_client_doc(request, document_pk):
    """Skip client extraction confirmation for a ProtectedClientDocument."""
    user = request.user

    if not get_policy().can_curate_clients(user):
        return HttpResponse("Permission denied", status=403)

    document = get_object_or_404(ProtectedClientDocument, pk=document_pk)
    document.client_extraction_status = "skipped"
    document.save(update_fields=["client_extraction_status"])

    return HttpResponse("", content_type="text/html")


@otp_required
def hx_close_modal(request):
    """Just close the modal without changing any status."""
    return HttpResponse(
        '<script>document.querySelectorAll(".modal-backdrop").forEach(b => b.remove());</script>',
        content_type="text/html",
    )


@otp_required
def hx_trigger_client_extraction_client_doc(request, document_pk):
    """
    Manually trigger client extraction for a single ProtectedClientDocument.

    This allows brokers to run the ClientMatcher independently of the upload process.
    """
    from data_room import hooks

    user = request.user

    if not get_policy().can_curate_clients(user):
        return HttpResponse("Permission denied", status=403)

    document = get_object_or_404(ProtectedClientDocument, pk=document_pk)

    # Start extraction (task will handle retry if document is still being indexed).
    # No configured task (DATA_ROOM_CLIENT_EXTRACTION_TASK=None) => noop render.
    from django.conf import settings

    client_extraction_task = hooks.get_client_extraction_task()
    if client_extraction_task is not None:
        client_extraction_task.delay(
            document_id=document.pk,
            user_id=user.pk,
            document_model="ProtectedClientDocument",
        )

        # In DEBUG/eager mode, task runs synchronously and already set the final status
        # Only set to "processing" in production when task runs async
        if not settings.DEBUG and not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            document.client_extraction_status = "processing"
            document.save(update_fields=["client_extraction_status"])
    else:
        logger.warning("Client extraction trigger skipped: no client extraction task configured.")

    # Refresh from DB
    document.refresh_from_db()

    # Determine which template to use based on request parameter
    view_type = request.GET.get("view", "card")
    if view_type == "table":
        template = "data_room/_show_company_document_table_row.html"
    else:
        template = "data_room/_show_company_document_card.html"

    return render(
        request,
        template,
        {"protected_document": document, "user": user},
    )
