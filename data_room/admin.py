import logging

from django.contrib import admin, messages
from django.http import HttpRequest

from .models import ProjectCompanyLink, ProjectZip, ProtectedClientDocument, ProtectedProjectDocument
from .tasks import extract_markdown_task, index_queried_documents_task
from .tasks.index_document import process_document_pipeline_task

# Statuses where a pipeline worker is actively processing — admin requeue
# actions must not reset these, else a second worker will race.
ACTIVE_INDEXING_STATUSES = ("queued", "processing", "chunking", "indexing")

logger = logging.getLogger(__name__)


@admin.register(ProtectedProjectDocument)
class ProtectedProjectDocumentAdmin(admin.ModelAdmin):
    model = ProtectedProjectDocument
    list_display = [
        "id",
        "name",
        "project",
        "user_type",
        "user_company",
        "tokens",
        "indexing_status",
        "reviewed",
        "disabled",
        "indexed_chunks",
        "document_type",
        "use_ai",
        "date_created",
    ]
    list_filter = (
        "project",
        "date_created",
        "date_updated",
        "user_type",
        "indexing_status",
        "reviewed",
        "disabled",
        "type",
        "use_ai",
        "document_type",
    )
    list_editable = ("use_ai",)
    search_fields = ["name", "user_company", "project__name"]
    readonly_fields = ["size", "date_created", "date_updated"]
    actions = [
        "rerun_full_pipeline",
        "restart_indexing",
        "reextract_markdown",
        "mark_as_reviewed",
    ]

    @admin.action(description="Re-run full extraction pipeline (LLM + Milvus)")
    def rerun_full_pipeline(self, request: HttpRequest, queryset):
        """Reset all extraction + indexing state and trigger the full pipeline."""
        safe_qs = queryset.exclude(file="").exclude(indexing_status__in=ACTIVE_INDEXING_STATUSES)
        active_count = queryset.filter(indexing_status__in=ACTIVE_INDEXING_STATUSES).count()
        doc_ids = list(safe_qs.values_list("id", flat=True))
        if not doc_ids:
            messages.info(
                request,
                (
                    f"Keine Dokumente zum Re-Run (aktiv in Pipeline: {active_count})."
                    if active_count
                    else "Keine Dokumente ausgewaehlt."
                ),
            )
            return

        ProtectedProjectDocument.objects.filter(id__in=doc_ids).update(
            indexing_status="pending",
            extraction_status="pending",
            financing_object_json=None,
            risk_factors_json=None,
            markdown="",
            tokens=0,
        )
        process_document_pipeline_task.delay(doc_ids, request.user.id, "ProtectedProjectDocument")
        msg = f"Volle Pipeline fuer {len(doc_ids)} Dokument(e) gestartet."
        if active_count:
            msg += f" ({active_count} ueberspringe(n) — aktiv in Pipeline)"
        messages.success(request, msg)

    def restart_indexing(self, request: HttpRequest, queryset):
        safe_qs = queryset.exclude(indexing_status__in=ACTIVE_INDEXING_STATUSES)
        active_count = queryset.filter(indexing_status__in=ACTIVE_INDEXING_STATUSES).count()
        object_ids = list(safe_qs.values_list("id", flat=True))

        if not object_ids:
            messages.info(
                request,
                (
                    f"Keine Dokumente zum Re-Index (aktiv in Pipeline: {active_count})."
                    if active_count
                    else "No documents selected for indexing."
                ),
            )
            return

        safe_qs.update(indexing_status="pending")
        index_queried_documents_task.delay(object_ids, request.user.id)
        messages.success(
            request,
            f"Queued {len(object_ids)} document(s) for re-indexing.",
        )

    restart_indexing.short_description = "Restart indexing for selected documents"

    @admin.action(description="[Legacy PyMuPDF] Re-extract Markdown only (no LLM)")
    def reextract_markdown(self, request: HttpRequest, queryset):
        """DEPRECATED: Nutzt PyMuPDF4LLM. Fuer LLM-basierte Extraktion `rerun_full_pipeline`."""
        queued = 0
        for doc in queryset.exclude(file=""):
            doc.markdown = ""
            doc.save(skip_preview=True)
            extract_markdown_task.delay(doc.id, request.user.id, "ProtectedDocument")
            queued += 1
        messages.success(request, f"Markdown re-extraction queued for {queued} document(s).")

    def mark_as_reviewed(self, request: HttpRequest, queryset):
        updated_count = 0
        for document in queryset:
            document.reviewed = True
            document.use_ai = True
            document.save(update_fields=["reviewed", "use_ai"])
            updated_count += 1
        messages.success(request, f"Marked {updated_count} document(s) as reviewed.")

    mark_as_reviewed.short_description = "Mark selected documents as reviewed"


class ProtectedProjectDocumentInline(admin.TabularInline):
    model = ProtectedProjectDocument
    extra = 0


@admin.register(ProtectedClientDocument)
class ProtectedClientDocumentAdmin(admin.ModelAdmin):
    model = ProtectedClientDocument
    list_display = [
        "id",
        "name",
        "client",
        "tokens",
        "indexing_status",
        "reviewed",
        "disabled",
        "document_type",
        "statement_type",
        "client_extraction_status",
        "guv_extracted",
        "bilanz_extracted",
        "review_status",
        "review_runs_count",
        "review_score",
        "user_type",
        "user_company",
        "date_created",
    ]
    list_filter = (
        "client",
        "date_created",
        "date_updated",
        "user_type",
        "indexing_status",
        "reviewed",
        "disabled",
        "document_type",
        "statement_type",
        "client_extraction_status",
        "guv_extracted",
        "bilanz_extracted",
        "review_status",
    )

    @admin.display(description="runs", ordering=None)
    def review_runs_count(self, obj) -> int:
        return len(obj.review_runs or [])

    list_editable = ("document_type", "statement_type", "reviewed", "disabled")
    search_fields = ["name", "user_company", "client__company"]
    readonly_fields = ["size", "date_created", "date_updated"]
    actions = [
        "rerun_full_pipeline",
        "restart_indexing",
        "reextract_markdown",
        "re_extract_and_remap",
    ]

    @admin.action(description="Re-Extract + Re-Map (force=True, side-granular)")
    def re_extract_and_remap(self, request: HttpRequest, queryset):
        """Plan-M3: Side-granular Re-Extract via admin_actions helper.
        Setzt guv/bilanz_mapping_status='re_mapping' und dispatcht
        extract_document_data_task(force=True) pro Doc."""
        from data_room.admin_actions import re_extract_and_remap_documents

        doc_ids = list(queryset.values_list("id", flat=True))
        if not doc_ids:
            messages.info(request, "Keine Dokumente ausgewaehlt.")
            return
        count = re_extract_and_remap_documents(doc_ids)
        messages.success(request, f"Re-Extract + Re-Map fuer {count} Dokument(e) gestartet.")

    @admin.action(description="Re-run full extraction pipeline (LLM + Milvus)")
    def rerun_full_pipeline(self, request: HttpRequest, queryset):
        """Reset all extraction + indexing state and trigger the full pipeline."""
        # Skip docs currently mid-pipeline — resetting their status would race
        # with the in-flight worker and duplicate LLM/Milvus work.
        safe_qs = queryset.exclude(file="").exclude(indexing_status__in=ACTIVE_INDEXING_STATUSES)
        active_count = queryset.filter(indexing_status__in=ACTIVE_INDEXING_STATUSES).count()
        doc_ids = list(safe_qs.values_list("id", flat=True))
        if not doc_ids:
            messages.info(
                request,
                (
                    f"Keine Dokumente zum Re-Run (aktiv in Pipeline: {active_count})."
                    if active_count
                    else "Keine Dokumente ausgewaehlt."
                ),
            )
            return

        ProtectedClientDocument.objects.filter(id__in=doc_ids).update(
            indexing_status="pending",
            client_extraction_status="pending",
            financial_extraction_status="pending",
            guv_json=None,
            bilanz_json=None,
            company_info_json=None,
            extracted_clients_data=[],
            guv_extracted=False,
            bilanz_extracted=False,
            markdown="",
            tokens=0,
        )
        process_document_pipeline_task.delay(doc_ids, request.user.id, "ProtectedClientDocument")
        msg = f"Volle Pipeline fuer {len(doc_ids)} Dokument(e) gestartet."
        if active_count:
            msg += f" ({active_count} ueberspringe(n) — aktiv in Pipeline)"
        messages.success(request, msg)

    @admin.action(description="Restart indexing (keep extraction data)")
    def restart_indexing(self, request: HttpRequest, queryset):
        """Nur Milvus-Indexing neu. Extraktions-Daten bleiben via Pipeline-Guards intakt."""
        safe_qs = queryset.exclude(indexing_status__in=ACTIVE_INDEXING_STATUSES)
        active_count = queryset.filter(indexing_status__in=ACTIVE_INDEXING_STATUSES).count()
        object_ids = list(safe_qs.values_list("id", flat=True))
        if not object_ids:
            messages.info(
                request,
                (
                    f"Keine Dokumente zum Re-Index (aktiv in Pipeline: {active_count})."
                    if active_count
                    else "Keine Dokumente ausgewaehlt."
                ),
            )
            return
        safe_qs.update(indexing_status="pending")
        process_document_pipeline_task.delay(object_ids, request.user.id, "ProtectedClientDocument")
        msg = f"Re-Indexing fuer {len(object_ids)} Dokument(e) gestartet."
        if active_count:
            msg += f" ({active_count} ueberspringe(n) — aktiv in Pipeline)"
        messages.success(request, msg)

    @admin.action(description="[Legacy PyMuPDF] Re-extract Markdown only (no LLM)")
    def reextract_markdown(self, request: HttpRequest, queryset):
        """DEPRECATED: Nutzt PyMuPDF4LLM. Fuer LLM-basierte Extraktion `rerun_full_pipeline`."""
        queued = 0
        for doc in queryset.exclude(file=""):
            doc.markdown = ""
            doc.save(skip_preview=True)
            extract_markdown_task.delay(doc.id, request.user.id, "ProtectedClientDocument")
            queued += 1
        messages.success(request, f"Markdown re-extraction queued for {queued} document(s).")


class ProjectZipAdmin(admin.ModelAdmin):
    model = ProjectZip
    list_display = ["id", "project", "user", "user_company", "status", "date_created"]
    list_filter = ["status", "date_created"]
    search_fields = ["project__name", "user__email", "user__username"]
    ordering = ["-date_created"]
    date_hierarchy = "date_created"

    def filename(self, obj):
        return obj.filename()

    filename.short_description = "ZIP File"

    def user_company(self, obj):
        return ProjectZip.user_company(obj)

    user_company.short_description = "User Company"


admin.site.register(ProjectZip, ProjectZipAdmin)


@admin.register(ProjectCompanyLink)
class ProjectCompanyLinkAdmin(admin.ModelAdmin):
    list_display = ["id", "project", "client", "is_active", "date_created"]
    list_filter = ["is_active", "date_created"]
    search_fields = ["project__name", "client__company"]
    raw_id_fields = ["project", "client"]
