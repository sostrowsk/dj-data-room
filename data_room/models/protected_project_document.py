# data_room/models/protected_project_document.py
import logging

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from data_room.models import ProtectedBaseDocument
from data_room.models.choices import ProjectDocumentType
from data_room.policies import get_policy

logger = logging.getLogger(__name__)


class ProtectedProjectDocument(ProtectedBaseDocument):
    """Document associated with a project."""

    project = models.ForeignKey(
        getattr(settings, "DATA_ROOM_PROJECT_MODEL", "project.Project"),
        related_name="protected_documents",
        on_delete=models.CASCADE,
    )
    document_type = models.CharField(
        _("Document Type"),
        max_length=32,
        choices=ProjectDocumentType.choices,
        blank=True,
        null=True,
    )
    # Extraction status
    EXTRACTION_STATUS_CHOICES = [
        ("pending", _("Pending")),
        ("processing", _("Processing")),
        ("completed", _("Completed")),
        ("failed", _("Failed")),
        ("skipped", _("Skipped")),
    ]
    extraction_status = models.CharField(
        _("Extraction status"),
        max_length=24,
        choices=EXTRACTION_STATUS_CHOICES,
        default="pending",
    )
    # Extracted JSON data from unified extraction
    financing_object_json = models.JSONField(
        _("Financing Object JSON"),
        blank=True,
        null=True,
        help_text=_("Extracted financing object data as JSON"),
    )
    risk_factors_json = models.JSONField(
        _("Risk Factors JSON"),
        blank=True,
        null=True,
        help_text=_("Extracted risk factors as JSON"),
    )

    class Meta:
        verbose_name = _("Protected Project Document")
        verbose_name_plural = _("Protected Project Documents")

    def check_permissions(self, user):
        """Check if user has permission to access this project document.

        Delegates to the active permission policy (``DATA_ROOM_PERMISSION_POLICY``)."""
        return get_policy().can_view_project_document(user, self)
