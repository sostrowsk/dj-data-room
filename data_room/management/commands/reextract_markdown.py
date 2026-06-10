import logging

from django.core.management.base import BaseCommand

from data_room.models import ProtectedClientDocument, ProtectedProjectDocument
from data_room.tasks import extract_markdown_task

logger = logging.getLogger(__name__)

MODEL_MAP = {
    "client": ("ProtectedClientDocument", ProtectedClientDocument),
    "project": ("ProtectedDocument", ProtectedProjectDocument),
}


class Command(BaseCommand):
    help = "Clear markdown and re-trigger extraction for specified documents"

    def add_arguments(self, parser):
        parser.add_argument(
            "--model",
            type=str,
            choices=["client", "project"],
            default="client",
            help="Document model: client or project",
        )
        parser.add_argument(
            "--document-ids",
            nargs="+",
            type=int,
            help="Specific document IDs to re-extract",
        )
        parser.add_argument(
            "--document-type",
            type=str,
            help="Filter by document_type (e.g. interim_figures, annual_report)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be re-extracted without making changes",
        )

    def handle(self, *args, **options):
        model_key = options["model"]
        model_name, model_class = MODEL_MAP[model_key]
        document_ids = options.get("document_ids")
        document_type = options.get("document_type")
        dry_run = options["dry_run"]

        queryset = model_class.objects.all()

        if document_ids:
            queryset = queryset.filter(id__in=document_ids)
        if document_type:
            queryset = queryset.filter(document_type=document_type)

        # Only re-extract documents that have a file
        queryset = queryset.exclude(file="")

        count = queryset.count()
        if count == 0:
            self.stdout.write(self.style.WARNING("No documents found matching criteria"))
            return

        self.stdout.write(f"Found {count} documents to re-extract markdown")

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN - No changes will be made"))
            for doc in queryset:
                has_md = "yes" if doc.markdown else "no"
                self.stdout.write(f"  ID {doc.id}: {doc.title} (has markdown: {has_md})")
            return

        for doc in queryset:
            old_len = len(doc.markdown) if doc.markdown else 0
            doc.markdown = ""
            doc.save(skip_preview=True)
            extract_markdown_task.delay(doc.id, None, model_name)
            self.stdout.write(f"  ID {doc.id}: cleared {old_len} chars, queued extraction")

        self.stdout.write(self.style.SUCCESS(f"Queued {count} documents for markdown re-extraction"))
