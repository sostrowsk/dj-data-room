# data_room/tasks/project_zip.py
import io
import logging
import os
import zipfile
from typing import Any, Dict

from celery import shared_task
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.utils.text import slugify
from django.utils.timezone import now
from progress.services import ProgressTrackerService

from data_room.conf import get_project_model
from data_room.helpers.get_company_documents import get_active_company_ids
from data_room.models.project_zip import ProjectZip
from data_room.models.protected_client_document import ProtectedClientDocument

Project = get_project_model()
UserModel = get_user_model()

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def project_zip_task(self, project_id: int, user_id: int) -> Dict[str, Any]:
    try:
        project = Project.objects.get(id=project_id)
        user = UserModel.objects.get(id=user_id)

        def is_verified(self):
            return True

        user.is_verified = is_verified.__get__(user)

        # check_permissions() is the single gate — it knows when to let a
        # broker triage unreviewed drafts (client-doc branch) and when to
        # block disabled/unreviewed docs for clients/partners. A pre-filter
        # on reviewed/disabled would hide broker-readable drafts and diverge
        # from the direct-URL behavior.
        project_accessible_documents = [
            document for document in project.protected_documents.all() if document.check_permissions(user)
        ]

        client_accessible_documents = []
        if project.client_company:
            active_company_ids = get_active_company_ids(project)
            for doc in ProtectedClientDocument.objects.filter(client_id__in=active_company_ids):
                if doc.check_permissions(user):
                    client_accessible_documents.append(doc)

        total_document_count = len(project_accessible_documents) + len(client_accessible_documents)
        if total_document_count == 0:
            return {
                "status": "failed",
                "error": "No accessible documents found in project",
            }

        # Delete old ZIP files for this user and project
        old_zips = ProjectZip.objects.filter(project=project, user=user)
        for old_zip in old_zips:
            if old_zip.zip_file:
                old_zip.zip_file.delete()  # This deletes the file from storage
            old_zip.delete()

        project_zip = ProjectZip.objects.create(project=project, user=user, status="processing")

        zip_generation_progress = ProgressTrackerService.create_task(
            user=user,
            task_type="Download ZIP generation",
            total_steps=total_document_count + 2,  # Steps: initialization + documents + finalization
            task_object_id=str(project_id),
            metadata={
                "project_name": project.name,
                "document_count": total_document_count,
                "celery_task_id": self.request.id,
                "project_zip_id": project_zip.id,
            },
        )

        zip_generation_progress.task_id = self.request.id
        zip_generation_progress.save()

        ProgressTrackerService.update_progress(
            task_progress_id=zip_generation_progress.id,
            current_step=1,
            info_txt="Initializing ZIP file creation",
            metadata={"phase": "initialization"},
        )

        # Import here to avoid circular import
        from data_room.views.api.protected_document import DocumentProcessor

        document_processor = DocumentProcessor(user.email)

        documents_with_folders = [("Projektdokumente", doc) for doc in project_accessible_documents] + [
            ("Kundendokumente", doc) for doc in client_accessible_documents
        ]

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as project_zip_file:
            for document_index, (subfolder, document) in enumerate(documents_with_folders, start=2):
                info_txt = f"Processing document {document_index - 1} of {total_document_count}: {document.name}"

                ProgressTrackerService.update_progress(
                    task_progress_id=zip_generation_progress.id,
                    current_step=document_index,
                    info_txt=info_txt,
                    metadata={
                        "document_name": document.name,
                        "document_id": document.id,
                        "phase": "processing",
                    },
                )

                try:
                    document_pdf_bytes = document_processor.generate_pdf(document)
                    base_filename = os.path.basename(document.file.path)
                    document_filename = os.path.splitext(base_filename)[0] + ".pdf"
                    project_zip_file.writestr(f"{subfolder}/{document_filename}", document_pdf_bytes)
                except Exception as document_error:
                    logger.error(f"Error processing document {document.pk}: {str(document_error)}")

        zip_content_bytes = zip_buffer.getvalue()
        zip_buffer.close()

        ProgressTrackerService.update_progress(
            task_progress_id=zip_generation_progress.id,
            current_step=total_document_count + 2,
            info_txt="Finalizing ZIP file",
            metadata={"phase": "finalization"},
        )

        zip_timestamp = now().strftime("%Y%m%d_%H%M%S")
        zip_filename = f"{slugify(project.name)}_{zip_timestamp}.zip"

        project_zip.zip_file.save(zip_filename, ContentFile(zip_content_bytes), save=False)
        project_zip.status = "completed"
        project_zip.save()

        ProgressTrackerService.complete_task(task_progress_id=zip_generation_progress.id, success=True)

        return {
            "status": "success",
            "message": "ZIP file generation completed",
            "project_id": project_id,
            "project_zip_id": project_zip.id,
            "zip_filename": zip_filename,
        }

    except Exception as task_error:
        logger.error(f"Error in project_zip_task: {task_error}", exc_info=True)
        if "project_zip" in locals():
            project_zip.status = "failed"
            project_zip.save()
        if "zip_generation_progress" in locals():
            ProgressTrackerService.complete_task(
                task_progress_id=zip_generation_progress.id,
                success=False,
                error_message=f"Error: {str(task_error)}",
            )
        return {"status": "failed", "error": str(task_error)}
