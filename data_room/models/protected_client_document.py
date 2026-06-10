# data_room/models/protected_client_document.py
import logging

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from data_room.hooks import get_accounting_framework_codes
from data_room.models import ProtectedBaseDocument
from data_room.models.choices import ClientDocumentType, StatementType
from data_room.policies import get_policy

logger = logging.getLogger(__name__)


def _accounting_framework_choices():
    """Build the choices tuple from the frameworks provider hook
    (DATA_ROOM_ACCOUNTING_FRAMEWORKS_PROVIDER, frozen fallback).

    Lazy: call at field-definition time so any future spec rename
    flows through without manual edits to this file."""
    return [(code, code) for code in get_accounting_framework_codes()]


class ProtectedClientDocument(ProtectedBaseDocument):
    """Document associated with a client company (not a specific project)."""

    client = models.ForeignKey(
        getattr(settings, "DATA_ROOM_CLIENT_COMPANY_MODEL", "users.ClientCompany"),
        related_name="protected_documents",
        on_delete=models.CASCADE,
    )
    document_type = models.CharField(
        _("Document Type"),
        max_length=32,
        choices=ClientDocumentType.choices,
        blank=True,
        null=True,
    )
    statement_type = models.CharField(
        max_length=15,
        choices=StatementType.choices,
        default=StatementType.SINGLE,
        verbose_name=_("Statement Type"),
        blank=True,
    )
    # Financial extraction flags
    guv_extracted = models.BooleanField(_("GuV extracted"), default=False)
    bilanz_extracted = models.BooleanField(_("Bilanz extracted"), default=False)
    # Financial extraction status
    FINANCIAL_EXTRACTION_STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("processing", _("Processing")),
        ("completed", _("Completed")),
        ("failed", _("Failed")),
        ("skipped", _("Skipped")),
    ]
    financial_extraction_status = models.CharField(
        _("Financial extraction status"),
        max_length=24,
        choices=FINANCIAL_EXTRACTION_STATUS_CHOICES,
        default="pending",
    )
    # Client extraction fields
    CLIENT_EXTRACTION_STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("processing", _("Processing")),
        ("awaiting_confirmation", _("Awaiting Confirmation")),
        ("completed", _("Completed")),
        ("failed", _("Failed")),
        ("skipped", _("Skipped")),
    ]
    client_extraction_status = models.CharField(
        _("Client extraction status"),
        max_length=24,
        choices=CLIENT_EXTRACTION_STATUS_CHOICES,
        default="pending",
    )
    extracted_clients_data = models.JSONField(
        _("Extracted clients data"),
        default=list,
        blank=True,
        help_text=_("Temporarily stores extracted client data awaiting user confirmation"),
    )
    # Extracted JSON data from unified extraction
    guv_json = models.JSONField(
        _("GuV JSON"),
        blank=True,
        null=True,
        help_text=_("Extracted GuV data as JSON"),
    )
    bilanz_json = models.JSONField(
        _("Bilanz JSON"),
        blank=True,
        null=True,
        help_text=_("Extracted Bilanz data as JSON"),
    )
    json_guv_mapped = models.JSONField(
        _("GuV JSON (mapped + classified)"),
        blank=True,
        null=True,
        help_text=_("guv_json enriched with per-position aggregate roles for verification"),
    )
    # Plan-M2: per-doc Bilanz-Slot-Mapping (Upload-Time). Symmetrisch zu
    # json_guv_mapped. Inhalt: {fiscal_year, slot_mapping, mapping_origin,
    # bilanz_mapping_schema_version}. Gefüllt vom Upload-Time-Heuristik+
    # LLM-Mapper. Memo-Pipeline liest dieses Feld direkt — kein Memo-
    # Time-LLM-Call mehr (M5).
    json_bilanz_mapped = models.JSONField(
        _("Bilanz JSON (mapped to slots)"),
        blank=True,
        null=True,
        help_text=_("Per-doc Bilanz-Slot-Mapping (Phase A scope: slot-based). " "Populated at upload time."),
    )
    pages_guv = models.JSONField(
        _("GuV pages (per statement_type)"),
        blank=True,
        null=True,
        help_text=_(
            "1-based PDF page numbers for the GuV section, keyed by "
            "statement_type ('single', 'consolidated', 'notes'). "
            "Consumed by the Phase-4 PDF slicer; missing/empty keys "
            "trigger a full-PDF fallback."
        ),
    )
    pages_bilanz = models.JSONField(
        _("Bilanz pages (per statement_type)"),
        blank=True,
        null=True,
        help_text=_(
            "1-based PDF page numbers for the Bilanz section, keyed by "
            "statement_type ('single', 'consolidated', 'notes'). Same "
            "fallback contract as pages_guv."
        ),
    )
    company_info_json = models.JSONField(
        _("Company Info JSON"),
        blank=True,
        null=True,
        help_text=_("Extracted company information as JSON"),
    )

    # ------------------------------------------------------------------
    # Phase E — accounting-framework persistence (Detection output)
    # ------------------------------------------------------------------
    accounting_framework = models.CharField(
        _("Accounting framework"),
        max_length=32,
        choices=_accounting_framework_choices(),
        null=True,
        blank=True,
        help_text=_("Detected accounting framework code (DE_HGB_GKV / AT_UGB_GKV / IFRS_NATURE / …)."),
    )
    accounting_framework_confidence = models.FloatField(
        _("Accounting framework confidence"),
        null=True,
        blank=True,
        help_text=_("Detection confidence 0.0-1.0; <0.5 logged but does not block."),
    )
    accounting_framework_evidence = models.JSONField(
        _("Accounting framework evidence"),
        null=True,
        blank=True,
        help_text=_("List of strings (cited indicators) that triggered detection."),
    )
    accounting_framework_spec_hash = models.CharField(
        _("Accounting framework spec_hash"),
        max_length=64,
        null=True,
        blank=True,
        help_text=_("SHA256 of the YAML frontmatter at detection time — invalidates cached mappings."),
    )
    json_guv_mapped_v2_preview = models.JSONField(
        _("GuV mapping (v2 preview, shadow field)"),
        blank=True,
        null=True,
        help_text=_(
            "Stage-3 shadow cache for the v2 spec-driven compiler. "
            "v1 reader/writer ignores it. Stage 4 cutover flips v2 to "
            "main `json_guv_mapped`; Stage 5 drops this field."
        ),
    )

    # ---------------------------------------------------------------
    # Plan-M3: Mapping-Status (Side-granular)
    # ---------------------------------------------------------------
    # GuV und Bilanz haben unabhängige Status — ein Doc kann z.B.
    # gültiges json_guv_mapped haben aber fehlendes json_bilanz_mapped
    # (oder umgekehrt: reiner Bilanz-Doc hat guv_mapping_status =
    # "not_applicable"). Memo-Pipeline rendert "—" für jede Side mit
    # Status != "ready". Existing-Data-Cut: alle Bestands-Docs starten
    # auf "mapping_missing" — werden via Admin-Action neu gemappt.
    MAPPING_STATUS_CHOICES = (
        ("ready", "Ready"),
        ("mapping_missing", "Mapping missing"),
        ("re_mapping", "Re-mapping in progress"),
        ("re_mapping_failed", "Re-mapping failed"),
        ("not_applicable", "Not applicable"),
        ("stamp_registry_mismatch", "Display-Stamp registry version mismatch"),
    )
    guv_mapping_status = models.CharField(
        _("GuV mapping status"),
        max_length=32,
        choices=MAPPING_STATUS_CHOICES,
        default="mapping_missing",
        db_index=True,
    )
    bilanz_mapping_status = models.CharField(
        _("Bilanz mapping status"),
        max_length=32,
        choices=MAPPING_STATUS_CHOICES,
        default="mapping_missing",
        db_index=True,
    )

    # ---------------------------------------------------------------
    # Self-Review-Loop fields (Phase D)
    # ---------------------------------------------------------------
    # Memo-pipeline does NOT read json_aggregate — audit/review only.
    # Canonical guv_json / bilanz_json / json_guv_mapped are still
    # the inputs to memo rendering.

    REVIEW_STATUS_CHOICES = (
        ("pending", "pending"),
        ("queued", "queued"),
        ("processing", "processing"),
        ("passed", "passed"),
        ("failed", "failed"),
        ("skipped", "skipped"),
        ("disabled", "disabled"),
        ("invalid_review_output", "invalid_review_output"),
    )

    json_aggregate = models.JSONField(
        _("Per-doc aggregate snapshot (review-only)"),
        blank=True,
        null=True,
        help_text=_(
            "Flat single-fiscal-year aggregate computed by the "
            "Self-Review-Loop. Audit/review artefact only — the memo "
            "renderer ignores this field."
        ),
    )
    review_status = models.CharField(
        _("Review-Loop status"),
        max_length=24,
        choices=REVIEW_STATUS_CHOICES,
        default="pending",
        help_text=_("State of the upload-time Self-Review-Loop for this doc."),
    )
    review_score = models.FloatField(
        _("Review-Loop combined score"),
        null=True,
        blank=True,
        help_text=_("0.0–1.0 weighted score of the best iteration."),
    )
    review_runs = models.JSONField(
        _("Review-Loop audit trail"),
        default=list,
        blank=True,
        help_text=_(
            "Per-iteration audit records: hard-check findings, LLM "
            "review result, combined_score, tokens, selected_as_best."
        ),
    )
    review_cycle_id = models.UUIDField(
        _("Review cycle id"),
        null=True,
        blank=True,
        help_text=_(
            "Per-cycle UUID. Re-uploading or force-reprocessing starts "
            "a new cycle and freezes new pre_review_snapshots."
        ),
    )
    json_guv_pre_review_snapshot = models.JSONField(
        _("GuV pre-review snapshot"),
        blank=True,
        null=True,
        help_text=_("Run-0 GuV extraction frozen for the current review_cycle_id."),
    )
    json_bilanz_pre_review_snapshot = models.JSONField(
        _("Bilanz pre-review snapshot"),
        blank=True,
        null=True,
        help_text=_("Run-0 Bilanz extraction frozen for the current review_cycle_id."),
    )
    json_guv_mapped_pre_review_snapshot = models.JSONField(
        _("Mapped GuV pre-review snapshot"),
        blank=True,
        null=True,
        help_text=_("Run-0 mapping frozen for the current review_cycle_id."),
    )
    pre_review_snapshot_meta = models.JSONField(
        _("Pre-review snapshot meta"),
        blank=True,
        null=True,
        help_text=_("{cycle_id, frozen_at, extraction_model, prompt_version} " "stamped at first freeze of the cycle."),
    )

    class Meta:
        verbose_name = _("Protected Client Document")
        verbose_name_plural = _("Protected Client Documents")

    def check_permissions(self, user):
        """Scoped read-permission for client documents.

        Delegates to the active permission policy (``DATA_ROOM_PERMISSION_POLICY``)."""
        return get_policy().can_view_client_document(user, self)
