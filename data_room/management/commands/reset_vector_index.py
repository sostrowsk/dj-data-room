import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from data_room.hooks import get_redis_lock_factory
from data_room.models import ProtectedProjectDocument

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reset vector database index by marking all documents as pending"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be reset without making changes",
        )
        parser.add_argument(
            "--filter-status",
            type=str,
            choices=["all", "indexed", "pending", "failed"],
            default="all",
            help="Only reset documents with specific status",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of documents to update per batch",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force reset even if another process is running (breaks lock)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        filter_status = options["filter_status"]
        batch_size = options["batch_size"]
        force = options.get("force", False)

        lock = None
        lock_factory = get_redis_lock_factory()
        if lock_factory is not None:
            try:
                redis_client = lock_factory()
                lock = redis_client.get_lock(
                    key="vector_index:reset",
                    timeout=1800,
                    auto_renew=True,
                    renewal_interval=30,
                    blocking=False,
                )

                if force and lock.is_locked():
                    self.stdout.write(self.style.WARNING("Force flag set - releasing existing lock"))
                    lock.force_release()

                lock_info = lock.get_info()
                if lock.is_locked() and not force:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Another reset process is already running.\n"
                            f"Lock holder: {lock_info['holder']}\n"
                            f"Time remaining: {lock_info['ttl']}s\n"
                            f"Use --force to override the lock (use with caution)"
                        )
                    )
                    return
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(
                        f"Redis lock not available: {str(e)}\n"
                        f"Proceeding without distributed lock (use with caution)"
                    )
                )
                lock = None

        queryset = ProtectedProjectDocument.objects.all()

        if filter_status != "all":
            queryset = queryset.filter(indexing_status=filter_status)

        total_count = queryset.count()

        if total_count == 0:
            self.stdout.write(self.style.WARNING("No documents found to reset"))
            return

        self.stdout.write(f"Found {total_count} documents to reset")

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN - No changes will be made"))
            self.stdout.write(f"Would reset {total_count} documents to 'pending' status")
            return

        if lock and not lock.acquire():
            self.stdout.write(self.style.ERROR("Failed to acquire lock for reset operation"))
            return

        updated = 0
        try:
            if lock:
                self.stdout.write(self.style.SUCCESS("Acquired exclusive lock for reset operation"))

            with transaction.atomic():
                for i in range(0, total_count, batch_size):
                    batch = queryset[i : i + batch_size]
                    batch_updated = batch.update(indexing_status="pending")
                    updated += batch_updated

                    if i + batch_size < total_count:
                        progress = (i + batch_size) / total_count * 100
                        self.stdout.write(f"Progress: {progress:.1f}% ({i + batch_size}/{total_count})")

                self.stdout.write(self.style.SUCCESS(f"Successfully reset {updated} documents to 'pending' status"))
        except Exception as e:
            logger.error(f"Error resetting vector index: {str(e)}")
            self.stdout.write(self.style.ERROR(f"Failed to reset vector index: {str(e)}"))
            raise
        finally:
            if lock and lock.is_owned():
                lock.release()
                self.stdout.write(self.style.NOTICE("Released lock"))
