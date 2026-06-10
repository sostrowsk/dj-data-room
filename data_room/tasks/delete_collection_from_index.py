# data_room/tasks/delete_collection_from_index.py
import asyncio
import logging

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils.timezone import now
from progress.services import ProgressTrackerService
from scribe.scribe_milvus import SCRIBE

from data_room.helpers.get_project import get_project

UserModel = get_user_model()

logger = logging.getLogger(__name__)

DELETION_STEPS = [
    "Initializing vector store",
    "Removing documents from index",
    "Cleaning up resources",
]


def delete_collection(scribe, project):
    """Drop the project namespace (Postgres always, Milvus best-effort)."""
    try:
        asyncio.run(scribe.drop_collection())
        logger.info(f"Successfully deleted documents for project ID: {project.id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete documents: {str(e)}")
        raise


def delete_collection_from_index_sync(project_id, progress_tracker_id):
    try:
        logger.info(f"Starting deletion task for project ID: {project_id}")
        project = get_project(project_id)
        collection_name = f"project_{project.id}"
        scribe = SCRIBE(collection_name)

        # Step 1: Initialize vector store
        ProgressTrackerService.update_progress(
            task_progress_id=progress_tracker_id,
            current_step=1,
            info_txt=DELETION_STEPS[0],
        )

        # Step 2: Delete documents from index
        try:
            ProgressTrackerService.update_progress(
                task_progress_id=progress_tracker_id,
                current_step=2,
                info_txt=DELETION_STEPS[1],
            )
            delete_collection(scribe, project)
            logger.info(f"All documents removed from index for project ID: {project_id}")
        except Exception as e:
            error_msg = f"Error during collection deletion: {str(e)}"
            logger.exception(error_msg)
            ProgressTrackerService.complete_task(
                task_progress_id=progress_tracker_id,
                success=False,
                error_message=error_msg,
            )
            raise

        # Step 3: Cleanup resources
        finally:
            ProgressTrackerService.update_progress(
                task_progress_id=progress_tracker_id,
                current_step=3,
                info_txt=DELETION_STEPS[2],
            )
            scribe.close()

        # Mark task as complete
        ProgressTrackerService.complete_task(task_progress_id=progress_tracker_id, success=True)

        return {
            "status": "success",
            "project_id": project_id,
            "message": "Collection deletion completed successfully",
        }

    except Exception as e:
        error_msg = f"Unexpected error in deletion task: {str(e)}"
        logger.exception(error_msg)
        try:
            ProgressTrackerService.complete_task(
                task_progress_id=progress_tracker_id,
                success=False,
                error_message=error_msg,
            )
        except Exception:
            logger.error("Failed to update progress tracker with error")
        raise


@shared_task(bind=True)
def delete_collection_from_index_task(self, project_id, user_id):
    task_id = self.request.id or str(now().timestamp())
    try:
        project = get_project(project_id)
        user = UserModel.objects.get(id=user_id)
    except Exception as e:
        logger.error(f"Failed to get project {project_id}: {e}")
        raise
    task_progress = ProgressTrackerService.create_task(
        user=user,
        task_type="Collection Deletion",
        total_steps=len(DELETION_STEPS),
        task_object_id=str(project_id),
        metadata={
            "project_id": project_id,
            "celery_task_id": task_id,
            "project_name": project.name,
        },
    )

    try:
        return delete_collection_from_index_sync(project_id, task_progress.id)
    except Exception as e:
        error_message = f"Failed to remove collection from index: {str(e)}"
        logger.error(error_message, exc_info=True)
        raise
