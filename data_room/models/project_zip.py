# data_room/models/project_zip.py
import os

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from data_room.storage import ProtectedFileField


class ProjectZip(models.Model):
    project = models.ForeignKey(
        getattr(settings, "DATA_ROOM_PROJECT_MODEL", "project.Project"),
        related_name="document_zips",
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    zip_file = ProtectedFileField(upload_to="zip", verbose_name=_("ZIP File"), max_length=255)
    status = models.CharField(_("Status"), max_length=20, default="processing")
    date_created = models.DateTimeField(_("Date created"), auto_now_add=True)

    class Meta:
        verbose_name = _("Project ZIP")
        verbose_name_plural = _("Project ZIPs")
        ordering = ("-date_created",)

    def __str__(self):
        return f"ZIP for {self.project.name} ({self.date_created.strftime('%Y-%m-%d')})"

    def filename(self):
        return os.path.basename(self.zip_file.name)

    @classmethod
    def user_company(cls, instance):
        """Get the company name for the user associated with this ProjectZip"""
        if instance.user:
            company = instance.user.get_company()
            if company:
                return str(company)
        return "-"


@receiver(post_delete, sender=ProjectZip)
def delete_project_zip_file(sender, instance, **kwargs):
    """Delete the file when ProjectZip instance is deleted"""
    if instance.zip_file:
        instance.zip_file.delete(save=False)
