import logging

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from data_room.tasks import count_indexed_chunks_task

logger = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    help = "Count indexed chunks in the vector database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="User ID to run the task as (defaults to first superuser)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be counted without starting the task",
        )
        parser.add_argument(
            "--async",
            action="store_true",
            default=True,
            help="Run counting asynchronously via Celery (default: True)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        run_async = options["async"]
        user_id = options.get("user_id")

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

        if dry_run:
            self.stdout.write(self.style.NOTICE("DRY RUN - No counting will be performed"))
            self.stdout.write(f"Would start chunk counting task for user ID: {user_id}")
            return

        try:
            if run_async:
                task = count_indexed_chunks_task.delay(user_id)
                self.stdout.write(self.style.SUCCESS(f"Chunk counting task started successfully (Task ID: {task.id})"))
                self.stdout.write("Monitor progress in Celery logs or through the progress tracker")
            else:
                self.stdout.write("Running counting synchronously...")
                self.stdout.write(self.style.WARNING("Note: Synchronous mode is not implemented. Use --async=True"))
        except Exception as e:
            logger.error(f"Error starting chunk counting: {str(e)}")
            self.stdout.write(self.style.ERROR(f"Failed to start chunk counting: {str(e)}"))
            raise
