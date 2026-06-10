import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

from data_room.models import ProtectedProjectDocument
from data_room.tasks import count_indexed_chunks_task, index_queried_documents_task

logger = logging.getLogger(__name__)


def reset_index(request) -> HttpResponse:
    if not request.user.is_superuser:
        raise PermissionDenied
    ProtectedProjectDocument.objects.update(indexing_status="pending")
    return redirect(reverse("admin:index"))


def index_documents(request) -> HttpResponse:
    if not request.user.is_superuser:
        raise PermissionDenied
    object_ids = list(ProtectedProjectDocument.objects.filter(indexing_status="pending").values_list("id", flat=True))

    if not object_ids:
        logger.info("No pending documents found to index on demand")
        messages.info(request, "No pending documents found to index.")
    else:
        if len(object_ids) > 100:
            messages.warning(
                request,
                f"Large batch ({len(object_ids)} documents) - processing may take significant time. "
                f"Consider processing in smaller batches if performance issues occur.",
            )

        index_queried_documents_task.delay(object_ids, request.user.id)
        messages.success(request, f"Queued {len(object_ids)} document(s) for indexing.")
        logger.info(f"Queued {len(object_ids)} documents for on-demand indexing")

    return redirect(reverse("admin:index"))


def count_indexed_chunks(request) -> HttpResponse:
    if not request.user.is_superuser:
        raise PermissionDenied
    count_indexed_chunks_task.delay(request.user.id)
    return redirect(reverse("admin:index"))
