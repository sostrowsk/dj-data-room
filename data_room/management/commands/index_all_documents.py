import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from data_room.hooks import get_redis_lock_factory
from data_room.tasks import index_queried_documents_task

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Index all documents in the vector database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID to run the task as (defaults to first superuser)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be indexed without starting the task",
        )
        parser.add_argument(
            "--async",
            action="store_true",
            default=True,
            help="Run indexing asynchronously via Celery (default: True)",
        )
        parser.add_argument(
            "--status",
            type=str,
            choices=["all", "pending", "failed", "indexed"],
            default="pending,failed",
            help="Filter documents by indexing status (default: pending,failed)",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of documents to index",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force indexing even if another process is running (breaks lock)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        run_async = options["async"]
        user_id = options.get("user_id")
        force = options.get("force", False)

        lock = None
        lock_factory = get_redis_lock_factory()
        if lock_factory is not None:
            try:
                redis_client = lock_factory()
                lock = redis_client.get_lock(
                    key="indexing:all_documents",
                    timeout=3600,
                    auto_renew=False,
                    blocking=False,
                )

                if force and lock.is_locked():
                    self.stdout.write(self.style.WARNING("Force flag set - releasing existing lock"))
                    lock.force_release()

                lock_info = lock.get_info()
                if lock.is_locked() and not force:
                    self.stdout.write(
                        self.style.ERROR(
                            f"Another indexing process is already running.\n"
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

        if not user_id:
            superuser = User.objects.filter(is_superuser=True).first()
            if not superuser:
                self.stdout.write(self.style.ERROR("No superuser found. Please specify --user-id"))
                return
            user_id = superuser.id
            self.stdout.write(f"Using superuser: {superuser.email} (ID: {user_id})")
        else:
            try:
                user = User.objects.get(id=user_id)
                if not user.is_superuser:
                    self.stdout.write(self.style.WARNING(f"User {user.email} is not a superuser"))
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User with ID {user_id} not found"))
                return

        # Get filter status
        status_filter = options.get("status", "pending,failed")
        limit = options.get("limit")

        # Build queryset based on status filter
        from data_room.models import ProtectedProjectDocument

        if status_filter == "all":
            queryset = ProtectedProjectDocument.objects.all()
        else:
            # Handle comma-separated status values
            if "," in status_filter:
                statuses = [s.strip() for s in status_filter.split(",")]
            else:
                statuses = [status_filter]
            queryset = ProtectedProjectDocument.objects.filter(indexing_status__in=statuses)

        # Apply limit if specified
        if limit:
            queryset = queryset[:limit]

        document_ids = list(queryset.values_list("id", flat=True))

        if not document_ids:
            self.stdout.write(self.style.WARNING(f"No documents found with status: {status_filter}"))
            return

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN - No indexing will be performed"))
            self.stdout.write(f"Would index {len(document_ids)} documents for user ID: {user_id}")
            return

        if lock:
            if not lock.acquire():
                self.stdout.write(self.style.ERROR("Failed to acquire lock for indexing operation"))
                return

        try:
            if run_async:
                self.stdout.write(f"Found {len(document_ids)} documents to index")
                if lock:
                    self.stdout.write(self.style.SUCCESS("Acquired exclusive lock for indexing"))
                task = index_queried_documents_task.delay(document_ids, user_id)
                self.stdout.write(
                    self.style.SUCCESS(f"Document indexing task started successfully (Task ID: {task.id})")
                )
                self.stdout.write("Monitor progress in Celery logs or through the progress tracker")
                if lock:
                    self.stdout.write(self.style.NOTICE("Lock will be automatically released when indexing completes"))
            else:
                self.stdout.write("Running indexing synchronously...")
                self.stdout.write(self.style.WARNING("Note: Synchronous mode is not implemented. Use --async=True"))
        except Exception as e:
            logger.error(f"Error starting document indexing: {str(e)}")
            self.stdout.write(self.style.ERROR(f"Failed to start document indexing: {str(e)}"))
            if lock:
                lock.release()
            raise
