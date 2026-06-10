# data_room/models/protected_base_document.py
import logging
import os
import tempfile
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from easy_thumbnails.files import get_thumbnailer
from pdf2image.pdf2image import convert_from_path
from scribe.tools.document_to_pdf import document_to_pdf
from scribe.utils import tiktoken_length

from data_room.storage import ProtectedFileField

from .choices import USER_TYPE_CHOICES

logger = logging.getLogger(__name__)


# Client extraction fields
CLIENT_EXTRACTION_STATUS_CHOICES = [
    ("pending", _("Pending")),
    ("processing", _("Processing")),
    ("awaiting_confirmation", _("Awaiting Confirmation")),
    ("completed", _("Completed")),
    ("failed", _("Failed")),
    ("skipped", _("Skipped")),
]


class ProtectedBaseDocument(models.Model):
    """Abstract base class for protected documents."""

    name = models.CharField(_("Name"), max_length=255)
    file = ProtectedFileField(
        upload_to="document",
        verbose_name=_("File (PDF, DOCX, DOC, ODT, GIF, PNG, JPG, JPEG, WEBP)"),
        validators=[FileExtensionValidator(["pdf", "docx", "doc", "odt", "gif", "png", "jpg", "jpeg", "webp"])],
        max_length=255,
    )
    original = ProtectedFileField(
        upload_to="original",
        verbose_name=_("Original file (DOCX, DOC, ODT)"),
        validators=[FileExtensionValidator(["pdf", "docx", "doc", "odt", "gif", "png", "jpg", "jpeg", "webp"])],
        blank=True,
        null=True,
        max_length=255,
    )
    type = models.CharField(max_length=255, blank=True)
    fiscal_year = models.IntegerField(_("Fiscal Year"), null=True, blank=True)
    fiscal_month = models.IntegerField(
        _("Fiscal Month"),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    use_ai = models.BooleanField(_("Use AI"), default=True)
    size = models.IntegerField(default=0)
    file_hash = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("File Hash (SHA256)"),
        help_text=_("SHA256 hash for duplicate detection"),
        db_index=True,
    )
    user_type = models.CharField(
        _("Created by user type"),
        max_length=8,
        choices=USER_TYPE_CHOICES,
        default="client",
    )
    user_company = models.CharField(_("Company name"), max_length=1024)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, blank=True, null=True, on_delete=models.CASCADE)
    preview = models.ImageField(upload_to="protected_document-preview", blank=True, null=True, max_length=255)
    markdown = models.TextField(blank=True)
    tokens = models.IntegerField(default=0)
    reviewed = models.BooleanField(_("Reviewed"), default=False)
    disabled = models.BooleanField(_("Disabled"), default=False)
    indexing_status = models.CharField(_("Indexing status"), max_length=16, default="pending")
    indexing_attempts = models.IntegerField(_("Indexing attempts"), default=0)
    indexed_chunks = models.IntegerField("Indexed chunks", default=0)
    company_info_json = models.JSONField(
        _("Company Info JSON"),
        blank=True,
        null=True,
        help_text=_("Extracted company information as JSON"),
    )
    date_created = models.DateTimeField(_("date_created"), auto_now_add=True)
    date_updated = models.DateTimeField(_("date_updated"), auto_now=True)

    class Meta:
        verbose_name = _("Protected Document")
        verbose_name_plural = _("Protected Documents")
        ordering = ("name",)
        abstract = True

    def __str__(self):
        return self.name or self.get_name()

    def save(self, *args, **kwargs):
        generate_preview = kwargs.pop("generate_preview", True)
        skip_preview = kwargs.pop("skip_preview", False)
        try:
            self.full_clean()
            self._update_file_metadata()
            if not self.pk or "file" in kwargs.get("update_fields", []):
                if self.type in ["docx", "doc", "odt"]:
                    try:
                        self.original.save(
                            Path(self.file.name).name,
                            ContentFile(self.file.read()),
                            save=False,
                        )
                    except Exception as e:
                        logger.error(f"Error saving original file: {str(e)}")
                    self._document_to_pdf()
            self._update_tokens()
            super().save(*args, **kwargs)
            if generate_preview and not skip_preview:
                self.generate_preview()
        except Exception as e:
            logger.error(f"Error in save method: {str(e)}")
            if not self.pk:
                super().save(*args, **kwargs)

    def _document_to_pdf(self):
        if self.type not in ["docx", "doc", "odt"]:
            return
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                with self.file.open("rb") as original_file:
                    file_content = original_file.read()
                input_path = Path(temp_dir) / self.file.name
                input_path.parent.mkdir(parents=True, exist_ok=True)
                with open(input_path, "wb") as f:
                    f.write(file_content)
                output_path = input_path.with_suffix(".pdf")
                document_to_pdf(input_path, output_path, self.type)
                with open(output_path, "rb") as converted_file:
                    content = ContentFile(converted_file.read())
                    self.file.save(f"{Path(self.file.name).stem}.pdf", content, save=False)
                self.type = "pdf"
                self.size = self.file.size
        except RuntimeError as e:
            if "pdflatex not found" in str(e):
                logger.error("PDF conversion failed: pdflatex/xelatex not installed")
                raise RuntimeError(
                    "PDF conversion requires pdflatex or xelatex. Please install texlive-latex-base package."
                )
            logger.error(f"Pandoc conversion failed: {str(e)}")
            raise
        except IOError as e:
            logger.error(f"File operation failed: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during PDF conversion: {str(e)}")
            raise

    def _update_file_metadata(self) -> None:
        if not self.pk:
            logger.info(f"File path (model): {self.file.path}")
        if not self.pk or not self.type or self.size == 0:
            self.type = self.get_type()
        if not self.pk or self.size == 0:
            self.size = self.get_size()
        if not self.name:
            self.name = self.get_name()
        if not self.file_hash and self.file:
            self.file_hash = self._calculate_file_hash()

    def _calculate_file_hash(self) -> str:
        """Calculate SHA256 hash of the file for duplicate detection."""
        import hashlib

        if not self.file:
            return ""

        try:
            sha256_hash = hashlib.sha256()
            self.file.seek(0)
            for chunk in iter(lambda: self.file.read(8192), b""):
                sha256_hash.update(chunk)
            self.file.seek(0)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating file hash: {str(e)}")
            return ""

    def _update_tokens(self) -> None:
        if not self.pk or not self.tokens:
            self.tokens = tiktoken_length(self.markdown)

    def get_absolute_url(self):
        return reverse("data_room:detail", args=(self.id,))

    def get_change_url(self):
        content_type = ContentType.objects.get_for_model(self.__class__)
        return reverse(
            "admin:%s_%s_change" % (content_type.app_label, content_type.model),
            args=(self.id,),
        )

    def get_name(self):
        return os.path.basename(self.file.name) if self.file.name else _("Unnamed Document")

    def get_type(self):
        if self.file.name:
            try:
                return os.path.splitext(self.file.name)[1].lower().replace(".", "")
            except FileNotFoundError:
                pass
        return "NN"

    def get_size(self):
        if self.file.name:
            try:
                return self.file.size
            except FileNotFoundError:
                pass
        return 0

    def get_page_count(self):
        if self.file.name:
            pass
        return None

    def generate_preview(self):
        try:
            if self.type == "pdf":
                self.generate_pdf_preview()
            elif self.type in ["gif", "png", "jpg", "jpeg", "webp"]:
                self._generate_image_preview()
        except Exception as e:
            logger.error(f"Error generating preview for document {self.id}: {str(e)}")

    def generate_pdf_preview(self):
        try:
            images = convert_from_path(self.file.path, first_page=1, last_page=1, size=(100, 100))
            if images:
                self._save_preview(images[0])
        except Exception as e:
            logger.error(f"Error generating PDF preview: {str(e)}")
            try:
                from PIL import Image, ImageDraw, ImageFont

                img = Image.new("RGB", (100, 100), color=(245, 245, 245))
                d = ImageDraw.Draw(img)
                try:
                    font = ImageFont.truetype("DejaVuSans.ttf", 12)
                except Exception:
                    font = ImageFont.load_default()
                d.text((10, 40), "PDF", fill=(80, 80, 80), font=font)
                self._save_preview(img)
            except Exception as fallback_error:
                logger.error(f"Fallback PDF preview also failed: {str(fallback_error)}")

    def _generate_image_preview(self):
        try:
            thumb_file = get_thumbnailer(self.file)["thumb100"]
            self.preview.save(thumb_file.name, thumb_file, save=False)
        except Exception as e:
            logger.error(f"Error generating image preview: {str(e)}")
            try:
                from PIL import Image

                with Image.open(self.file.path) as img:
                    img.thumbnail((100, 100))
                    self._save_preview(img)
            except Exception as fallback_error:
                logger.error(f"Fallback image preview also failed: {str(fallback_error)}")
                self.__class__.objects.filter(pk=self.pk).update(indexing_status="indexed")

    def _save_preview(self, image):
        thumb_io = BytesIO()
        image.save(thumb_io, "JPEG", optimize=True, quality=85)
        self.preview.save(f"{slugify(self.name)}_preview.jpg", File(thumb_io), save=False)
        self.__class__.objects.filter(pk=self.pk).update(preview=self.preview.name)

    def get_preview(self):
        if not self.preview and not self.preview.storage.exists(self.preview.name):
            self.generate_preview()
        return self.preview.name if self.preview else ""

    @property
    def display_size(self):
        size = self.size or self.get_size()
        if size > 1048576:
            return f"{size / 1048576:.1f} MB"
        return f"{size / 1024:.1f} KB"

    TYPE_CHOICES = [
        ("client", _("Client")),
        ("partner", _("Partner")),
        ("admin", _("Admin")),
    ]
