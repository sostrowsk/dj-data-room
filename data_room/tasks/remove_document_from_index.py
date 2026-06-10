# data_room/tasks/remove_document_from_index.py
import asyncio
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils.timezone import now
from progress.services import ProgressTrackerService
from scribe.scribe_milvus import SCRIBE

UserModel = get_user_model()

logger = logging.getLogger(__name__)


def remove_document_sync(scribe, document_id):
    """Delete the document's chunks (Postgres always, Milvus best-effort)."""
    try:
        asyncio.run(scribe.delete_documents(document_id=document_id))
        logger.info(f"Successfully removed document {document_id} from index")
        return True
    except Exception as e:
        logger.error(f"Failed to remove document: {str(e)}")
        raise


def remove_document_from_index_sync(protected_document_id, progress_tracker_id, collection_name):
    try:
        ProgressTrackerService.update_progress(
            task_progress_id=progress_tracker_id,
            current_step=1,
            info_txt="Preparing document removal",
        )
        scribe = SCRIBE(collection_name)

        ProgressTrackerService.update_progress(
            task_progress_id=progress_tracker_id,
            current_step=2,
            info_txt="Initializing vector store",
        )

        try:
            ProgressTrackerService.update_progress(
                task_progress_id=progress_tracker_id,
                current_step=3,
                info_txt="Removing document from vector store",
            )

            remove_document_sync(scribe, protected_document_id)
            ProgressTrackerService.update_progress(
                task_progress_id=progress_tracker_id,
                current_step=4,
                info_txt="Cleanup and finalization",
            )
            logger.info(f"Document removed from index for document ID: {protected_document_id}")
            ProgressTrackerService.complete_task(task_progress_id=progress_tracker_id, success=True)

            return {
                "status": "success",
                "document_id": protected_document_id,
                "message": "Document successfully removed from index",
            }

        except Exception as e:
            error_msg = f"Error during document removal from index: {str(e)}"
            logger.exception(error_msg)
            ProgressTrackerService.complete_task(
                task_progress_id=progress_tracker_id,
                success=False,
                error_message=error_msg,
            )
            raise

        finally:
            scribe.close()

    except Exception as e:
        error_msg = f"Unexpected error in removal task: {str(e)}"
        logger.exception(error_msg)
        try:
            ProgressTrackerService.complete_task(
                task_progress_id=progress_tracker_id,
                success=False,
                error_message=error_msg,
            )
        except Exception as e:
            logger.error(f"Failed to update progress tracker with error: {e}")
        raise


@shared_task(bind=True)
def remove_document_from_index_task(self, protected_document_id, user_id, collection_name=None):
    task_id = self.request.id or str(now().timestamp())

    # Resolve collection_name: prefer explicit arg, fall back to DB lookup
    if not collection_name:
        try:
            # Try ProtectedProjectDocument first, then ProtectedClientDocument
            from data_room.models import ProtectedClientDocument, ProtectedProjectDocument

            try:
                doc = ProtectedProjectDocument.objects.get(id=protected_document_id)
                collection_name = f"project_{doc.project_id}"
            except ProtectedProjectDocument.DoesNotExist:
                doc = ProtectedClientDocument.objects.get(id=protected_document_id)
                collection_name = f"client_{doc.client_id}"
        except Exception as e:
            logger.error(f"Failed to resolve collection for document {protected_document_id}: {e}")
            raise

    try:
        user = UserModel.objects.get(id=user_id)
    except UserModel.DoesNotExist:
        logger.error(f"User with ID {user_id} not found for document removal task")
        user = UserModel.objects.filter(is_superuser=True).first()
        if not user:
            raise ValueError("No valid user found for document removal task")

    task_progress = ProgressTrackerService.create_task(
        user=user,
        task_type="Document Removal",
        total_steps=4,
        task_object_id=str(protected_document_id),
        metadata={
            "document_id": protected_document_id,
            "celery_task_id": task_id,
        },
    )

    try:
        return remove_document_from_index_sync(protected_document_id, task_progress.id, collection_name)
    except Exception as e:
        error_message = f"Failed to remove document from index: {str(e)}"
        logger.error(error_message, exc_info=True)
        raise
