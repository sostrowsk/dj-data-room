from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ProjectCompanyLink(models.Model):
    """Links a ClientCompany to a Project with an active/inactive flag for Info-Memo generation."""

    project = models.ForeignKey(
        getattr(settings, "DATA_ROOM_PROJECT_MODEL", "project.Project"),
        on_delete=models.CASCADE,
        related_name="company_links",
    )
    client = models.ForeignKey(
        getattr(settings, "DATA_ROOM_CLIENT_COMPANY_MODEL", "users.ClientCompany"),
        on_delete=models.CASCADE,
        related_name="project_links",
    )
    is_active = models.BooleanField(
        _("Active for Info-Memo"),
        default=True,
        help_text=_("Whether this company is included in Info-Memo generation for this project"),
    )
    date_created = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "client")
        verbose_name = _("Project Company Link")
        verbose_name_plural = _("Project Company Links")

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"{self.client} → {self.project} ({status})"
