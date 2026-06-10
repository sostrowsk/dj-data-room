import hashlib
import os

from django import forms
from django.core.validators import FileExtensionValidator
from django.utils.translation import gettext_lazy as _

from data_room.utils import slugify_de

from ..models import ProtectedClientDocument, ProtectedProjectDocument
from ..models.choices import ClientDocumentType, ProjectDocumentType


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = [single_file_clean(data, initial)]
        return result


class UploadProtectedDocumentForm(forms.ModelForm):
    document_type = forms.ChoiceField(
        choices=[("", _("Select type"))] + list(ProjectDocumentType.choices),
        required=False,
        help_text=_("Select a type to classify this document"),
    )

    class Meta:
        model = ProtectedProjectDocument
        fields = ("name", "file", "document_type")

    def __init__(self, *args, **kwargs):
        self.project = kwargs.pop("project", None)
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if file:
            file_name = file.name
            file_path = os.path.dirname(file_name)
            file_base = os.path.basename(file_name)
            file_root, file_ext = os.path.splitext(file_base)
            slugified_file_base = f"{slugify_de(file_root)}{file_ext.lower()}"
            slugified_file_name = os.path.join(file_path, slugified_file_base)
            if file.name != slugified_file_name:
                file.name = slugified_file_name
        return file

    def clean(self):
        cleaned_data = super().clean()
        file = cleaned_data.get("file")

        # Check for duplicates based on file hash
        if file and self.project:
            # Calculate hash of uploaded file
            file.seek(0)
            sha256_hash = hashlib.sha256()
            for chunk in iter(lambda: file.read(8192), b""):
                sha256_hash.update(chunk)
            file.seek(0)  # Reset file pointer
            file_hash = sha256_hash.hexdigest()

            # Check if document with same hash exists in this project
            existing_doc = ProtectedProjectDocument.objects.filter(project=self.project, file_hash=file_hash).first()

            if existing_doc:
                raise forms.ValidationError(_(f"Dieses Dokument existiert bereits im Projekt: {existing_doc.name}"))

        return cleaned_data


ALLOWED_EXTENSIONS = ["pdf", "docx", "doc", "odt", "gif", "png", "jpg", "jpeg", "webp"]


class UploadProtectedDocumentsForm(forms.Form):
    """Form for uploading multiple documents with drag-and-drop support."""

    DOCUMENT_TARGET_CHOICES = [
        ("project", _("Project Document")),
        ("client", _("Company Document")),
    ]

    files = MultipleFileField(
        label=_("Files"),
        validators=[FileExtensionValidator(ALLOWED_EXTENSIONS)],
        widget=MultipleFileInput(
            attrs={
                "class": "d-none",
                "id": "file-input",
                "accept": ",".join(f".{ext}" for ext in ALLOWED_EXTENSIONS),
            }
        ),
    )

    document_target = forms.ChoiceField(
        label=_("Document Type"),
        choices=DOCUMENT_TARGET_CHOICES,
        initial="project",
        widget=forms.RadioSelect(attrs={"class": "btn-check"}),
    )

    project_document_type = forms.ChoiceField(
        label=_("Category"),
        choices=[("", _("Select category"))] + list(ProjectDocumentType.choices),
        required=False,
    )

    client_document_type = forms.ChoiceField(
        label=_("Category"),
        choices=[("", _("Select category"))] + list(ClientDocumentType.choices),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        self.project = kwargs.pop("project", None)
        self.client = kwargs.pop("client", None)
        super().__init__(*args, **kwargs)

    def calculate_file_hash(self, file):
        """Calculate SHA256 hash of a file."""
        file.seek(0)
        sha256_hash = hashlib.sha256()
        for chunk in iter(lambda: file.read(8192), b""):
            sha256_hash.update(chunk)
        file.seek(0)
        return sha256_hash.hexdigest()

    def check_duplicate(self, file_hash, document_target):
        """Check if a document with the same hash already exists."""
        if document_target == "client" and self.client:
            return ProtectedClientDocument.objects.filter(
                client=self.client,
                file_hash=file_hash,
            ).first()
        if self.project:
            return ProtectedProjectDocument.objects.filter(
                project=self.project,
                file_hash=file_hash,
            ).first()
        return None

    def slugify_filename(self, file):
        """Slugify the filename while preserving extension."""
        file_name = file.name
        file_path = os.path.dirname(file_name)
        file_base = os.path.basename(file_name)
        file_root, file_ext = os.path.splitext(file_base)
        slugified_file_base = f"{slugify_de(file_root)}{file_ext.lower()}"
        return os.path.join(file_path, slugified_file_base)
