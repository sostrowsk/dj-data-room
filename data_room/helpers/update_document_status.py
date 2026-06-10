# data_room/helpers/update_document_status.py
import logging
from datetime import timedelta

from django.db import IntegrityError, models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Define valid state transitions
VALID_STATE_TRANSITIONS = {
    "pending": [
        "processing",
        "failed",
    ],  # From pending -> can go to processing or failed
    "processing": [
        "chunking",
        "failed",
    ],  # From processing -> can go to chunking or failed
    "chunking": ["indexing", "failed"],  # From chunking -> can go to indexing or failed
    "indexing": ["indexed", "failed"],  # From indexing -> can go to indexed or failed
    "indexed": ["pending"],  # From indexed -> can only go back to pending for reprocessing
    "failed": ["pending"],  # From failed -> can only go back to pending for retry
}

MAX_ATTEMPTS = 3
# Time (in seconds) that a document should stay in a particular state before being considered stuck
MAX_STATE_TIME = {
    "processing": 1800,  # 30 minutes
    "chunking": 3600,  # 1 hour
    "indexing": 7200,  # 2 hours
}


def check_stuck_documents():
    """Identify documents that are stuck in a processing state for too long."""
    from ..models import ProtectedProjectDocument

    now = timezone.now()
    stuck_docs = []

    for state, max_time in MAX_STATE_TIME.items():
        # Find documents in this state that haven't been updated in max_time
        time_threshold = now - timedelta(seconds=max_time)
        docs = ProtectedProjectDocument.objects.filter(indexing_status=state, date_updated__lt=time_threshold)
        if docs.exists():
            for doc in docs:
                logger.warning(f"Document ID {doc.id} ({doc.name}) stuck in '{state}' state since {doc.date_updated}")
                stuck_docs.append(doc.id)

            # Mark stuck documents as failed
            try:
                docs.update(
                    indexing_status="failed",
                    indexing_attempts=models.F("indexing_attempts") + 1,
                )
                logger.info(f"Marked {docs.count()} stuck documents as failed")
            except Exception as e:
                logger.error(f"Failed to mark stuck documents as failed: {str(e)}")

    return stuck_docs


def update_document_status(document, status, force=False):
    """
    Update the indexing status of a document with proper state transition validation.

    Args:
        document: The document object to update
        status: The new status to set
        force: If True, bypass transition validation (use with caution)

    Returns:
        bool: True if status was updated successfully, False otherwise
    """
    try:
        with transaction.atomic():
            current_status = document.indexing_status

            # Validate the state transition unless force=True
            if not force and status != current_status:
                if current_status not in VALID_STATE_TRANSITIONS:
                    logger.error(f"Invalid current state '{current_status}' for document ID {document.id}")
                    return False

                if status not in VALID_STATE_TRANSITIONS[current_status]:
                    logger.error(
                        f"Invalid state transition for document ID {document.id}: "
                        f"{current_status} -> {status}. Valid transitions: {VALID_STATE_TRANSITIONS[current_status]}"
                    )
                    return False

            # If setting to processing/chunking/indexing state, check if it's already in one of these states
            # BUT allow valid transitions according to VALID_STATE_TRANSITIONS
            active_states = ["processing", "chunking", "indexing"]
            if (
                status in active_states
                and current_status in active_states
                and status != current_status
                and status not in VALID_STATE_TRANSITIONS.get(current_status, [])
            ):
                logger.warning(
                    f"Document ID {document.id} already in active state '{current_status}'. "
                    f"Skipping transition to '{status}'."
                )
                return False

            # Update status with appropriate handling
            document.indexing_status = status

            # If the status is 'failed', increment the attempts counter.
            # Once MAX_ATTEMPTS is reached the doc must reach a terminal
            # state so Celery retries stop. The state stays 'failed'
            # (terminal) — VALID_STATE_TRANSITIONS already allows
            # 'failed' -> 'pending' for manual re-queue.
            if status == "failed":
                document.indexing_attempts += 1
                if document.indexing_attempts >= MAX_ATTEMPTS:
                    logger.warning(
                        f"Document ID {document.id} exceeded maximum indexing attempts ({MAX_ATTEMPTS}). "
                        f"Marking as failed (terminal)."
                    )
                    # Already 'failed' — explicit re-assignment for clarity.
                    document.indexing_status = "failed"

            # Skip preview generation when updating status to avoid recursive calls
            document.save(skip_preview=True)
            logger.info(f"Document ID {document.id} status updated: {current_status} -> {document.indexing_status}")
            return True

    except IntegrityError as e:
        logger.error(f"Database integrity error updating document status: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Failed to update document status: {str(e)}")
        # Emergency direct UPDATE: bypass the ORM (and any save() side-
        # effects like preview generation) to break a recursion loop.
        # Uses the concrete subclass's db_table — the original hardcoded
        # 'data_room_protecteddocument' did not exist (the abstract base
        # has no table; subclasses get protectedprojectdocument /
        # protectedclientdocument), so the fallback always errored.
        try:
            from django.db import connection

            table = type(document)._meta.db_table
            stmt = (
                f"UPDATE {table} SET indexing_status = %s, "
                "indexing_attempts = CASE WHEN indexing_status = 'failed' THEN indexing_attempts + 1 "
                "ELSE indexing_attempts END, date_updated = %s WHERE id = %s"
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    stmt,
                    [
                        # Codex P1 / staging-bug fix: terminal state on
                        # max-attempts is 'failed', not 'indexed'. Otherwise
                        # the doc gets stuck in 'pending' forever because
                        # 'indexed' -> 'failed' is an invalid transition.
                        (status if document.indexing_attempts < MAX_ATTEMPTS else "failed"),
                        timezone.now(),
                        document.id,
                    ],
                )
            return True
        except Exception as db_e:
            logger.error(f"Emergency status update also failed for document ID {document.id}: {str(db_e)}")
            return False
