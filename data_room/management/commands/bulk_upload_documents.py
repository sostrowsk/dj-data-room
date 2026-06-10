"""
Management command to bulk upload PDFs to a project.

Usage:
    python manage.py bulk_upload_documents --project-id 123 --directory /path/to/pdfs
    python manage.py bulk_upload_documents --project-id 123 --directory /path/to/pdfs --use-ai --extract-guv
"""

import hashlib
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from data_room.conf import get_project_model
from data_room.models import ProtectedProjectDocument

Project = get_project_model()


class Command(BaseCommand):
    help = "Bulk upload PDF documents to a project"

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-id",
            type=int,
            required=True,
            help="Project ID to upload documents to",
        )
        parser.add_argument(
            "--directory",
            "-d",
            type=str,
            required=True,
            help="Directory containing PDF files",
        )
        parser.add_argument(
            "--use-ai",
            action="store_true",
            help="Mark documents for AI processing (use_ai=True)",
        )
        parser.add_argument(
            "--extract-text",
            action="store_true",
            help="Extract text from PDFs to markdown (without vector indexing)",
        )
        parser.add_argument(
            "--pattern",
            type=str,
            default="*.pdf",
            help="File pattern to match (default: *.pdf)",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID for created documents and tasks (default: first superuser)",
        )

    def handle(self, *args, **options):
        project_id = options["project_id"]
        directory = Path(options["directory"])
        use_ai = options["use_ai"]
        extract_text = options["extract_text"]
        pattern = options["pattern"]
        user_id_option = options.get("user_id")

        # Get or validate user
        from django.contrib.auth import get_user_model

        User = get_user_model()

        if user_id_option:
            try:
                user = User.objects.get(id=user_id_option)
            except User.DoesNotExist:
                raise CommandError(f"User mit ID {user_id_option} nicht gefunden")
        else:
            user = User.objects.filter(is_superuser=True).first()
            if not user:
                raise CommandError("Kein Superuser gefunden. Bitte --user-id angeben oder einen Superuser erstellen.")

        self.stdout.write(f"Verwende User: {user.email} (ID: {user.id})")

        # Validate project
        try:
            project = Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            raise CommandError(f"Project mit ID {project_id} nicht gefunden")

        # Validate directory
        if not directory.exists():
            raise CommandError(f"Directory nicht gefunden: {directory}")

        if not directory.is_dir():
            raise CommandError(f"Kein Directory: {directory}")

        # Find PDF files
        pdf_files = sorted(directory.glob(pattern))
        if not pdf_files:
            self.stdout.write(self.style.WARNING(f"Keine Dateien gefunden mit Pattern: {pattern}"))
            return

        self.stdout.write(f"Uploading {len(pdf_files)} Datei(en) zu Project: {project.name} (ID: {project.id})")

        uploaded_count = 0
        skipped_count = 0
        error_count = 0
        document_ids = []

        for pdf_path in pdf_files:
            try:
                # Calculate file hash BEFORE upload for duplicate detection
                file_hash = self._calculate_hash(pdf_path)

                # Check if already uploaded (by hash in this project)
                existing_doc = ProtectedProjectDocument.objects.filter(project=project, file_hash=file_hash).first()

                if existing_doc:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Übersprungen (bereits vorhanden): {pdf_path.name} "
                            f"→ existiert als '{existing_doc.name}' (ID: {existing_doc.id})"
                        )
                    )
                    skipped_count += 1
                    continue

                # Determine user_company
                if project.client_company:
                    user_company = project.client_company.name
                else:
                    user_company = project.name  # Fallback to project name

                # Create document
                with open(pdf_path, "rb") as f:
                    doc = ProtectedProjectDocument.objects.create(
                        project=project,
                        name=pdf_path.stem,  # Filename without extension
                        file=File(f, name=pdf_path.name),
                        type="pdf",
                        use_ai=use_ai,
                        size=pdf_path.stat().st_size,
                        file_hash=file_hash,
                        user_type="client",
                        user_company=user_company,
                        user=user,  # Set the user for progress tracking
                    )
                    document_ids.append(doc.id)
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Hochgeladen: {pdf_path.name} (ID: {doc.id})"))
                    uploaded_count += 1

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Fehler bei {pdf_path.name}: {e}"))
                error_count += 1

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Hochgeladen: {uploaded_count} Datei(en)"))
        if skipped_count > 0:
            self.stdout.write(f"Übersprungen: {skipped_count} Datei(en)")

        # Extract text if requested
        if extract_text and document_ids:
            self.stdout.write("")
            self.stdout.write("Text-Extraktion wird gestartet...")

            from scribe.tools.ocr_processor import extract_markdown_with_ocr

            success_count = 0
            error_count = 0

            for doc_id in document_ids:
                try:
                    doc = ProtectedProjectDocument.objects.get(id=doc_id)
                    self.stdout.write(f"  Verarbeite: {doc.name}...", ending=" ")

                    # Extract markdown from PDF
                    markdown_text = extract_markdown_with_ocr(doc.file.path)

                    # Update document
                    doc.markdown = markdown_text
                    doc.tokens = len(markdown_text.split())  # Rough token count
                    doc.save(update_fields=["markdown", "tokens"])

                    self.stdout.write(self.style.SUCCESS(f"✓ ({len(markdown_text)} Zeichen)"))
                    success_count += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"✗ Fehler: {e}"))
                    error_count += 1

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"Text extrahiert: {success_count} Dokument(e)"))
            if error_count > 0:
                self.stdout.write(self.style.ERROR(f"Fehler: {error_count} Dokument(e)"))

    def _calculate_hash(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
