"""Admin-Actions for ProtectedClientDocument (Plan-M3).

Two modes:
- per-Doc: re_extract_and_remap_documents(doc_ids)
- per-Project (bulk): re_extract_and_remap_project_documents(project_ids)

Both transition guv_mapping_status + bilanz_mapping_status to
"re_mapping" BEFORE dispatching the extract task. JSON-Felder
(json_guv_mapped, json_bilanz_mapped) bleiben unangetastet — sie
werden erst nach erfolgreicher Side im Task überschrieben.

Side-granular Atomicity (Plan-M3 / User-Direktive):
- Task ruft `extract_document_data_task.delay(doc_id, force=True)`.
- Bei Erfolg pro Side: Feld neu + Status="ready".
- Bei Fehler pro Side: Feld unverändert + Status="re_mapping_failed".
- Side-Failures isoliert (GuV ok / Bilanz fail funktioniert).
"""

from __future__ import annotations

import logging
from typing import Iterable

from django.db import transaction

logger = logging.getLogger(__name__)


def _trigger_re_extract_for_doc(doc_id: int) -> bool:
    """Set both side-statuses to re_mapping atomically, then enqueue
    the extract task with force=True. JSON-Felder bleiben unverändert.

    Returns False (noop, statuses untouched) when no extraction task is
    configured (DATA_ROOM_EXTRACTION_TASK resolves to None)."""
    from data_room import hooks
    from data_room.models import ProtectedClientDocument

    extraction_task = hooks.get_extraction_task()
    if extraction_task is None:
        logger.warning(f"Re-extract for doc {doc_id} skipped: no extraction task configured.")
        return False

    with transaction.atomic():
        ProtectedClientDocument.objects.filter(id=doc_id).update(
            guv_mapping_status="re_mapping",
            bilanz_mapping_status="re_mapping",
        )
        transaction.on_commit(
            lambda: extraction_task.delay(
                doc_id,
                force=True,
            )
        )
    return True


def re_extract_and_remap_documents(doc_ids: Iterable[int]) -> int:
    """Per-Doc Admin-Action. Triggert force=True Re-Extract pro Doc-ID.

    Returns: count of dispatched tasks.
    """
    count = 0
    for doc_id in doc_ids:
        if _trigger_re_extract_for_doc(doc_id):
            count += 1
    return count


def re_extract_and_remap_project_documents(project_ids: Iterable[int]) -> int:
    """Per-Project Bulk-Action. Triggert force=True Re-Extract für alle
    ProtectedClientDocuments im aktiven Gruppen-Scope der angegebenen
    Projects.

    Plan-P2.B: Memo-Scope läuft über get_active_company_ids
    (data_room/helpers/get_company_documents.py) — Holding +
    Töchter/Schwestern (minus explizit deaktivierte
    ProjectCompanyLinks). Die Bulk-Action soll das gleiche Set
    abdecken; Filter über client__projects__id__in würde nur den
    direkten primary client_company erreichen.

    Returns: count of dispatched tasks across all projects.
    """
    from data_room.conf import get_project_model
    from data_room.helpers.get_company_documents import get_active_company_ids
    from data_room.models import ProtectedClientDocument

    Project = get_project_model()

    project_ids_list = list(project_ids)
    if not project_ids_list:
        return 0

    active_client_ids: set = set()
    projects = Project.objects.filter(id__in=project_ids_list).select_related("client_company")
    for project in projects:
        for cid in get_active_company_ids(project):
            active_client_ids.add(cid)

    if not active_client_ids:
        return 0

    count = 0
    docs = ProtectedClientDocument.objects.filter(client_id__in=active_client_ids).distinct()
    for doc in docs:
        if _trigger_re_extract_for_doc(doc.id):
            count += 1
    return count
