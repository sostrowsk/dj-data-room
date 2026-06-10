# data_room/views/hx/upload_protected_document.py
import hashlib
import logging
import os

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.translation import gettext as _
from django_otp.decorators import otp_required

from data_room.utils import slugify_de

from ...decorators import project_permission_required
from ...helpers import get_document_list_context
from ...models import ProtectedClientDocument, ProtectedProjectDocument
from ...models.choices import ClientDocumentType, ProjectDocumentType
from ...tasks import process_document_pipeline_task

logger = logging.getLogger(__name__)

UPLOAD_TEMPLATE = "data_room/_hx_upload_protected_document.html"
DOCUMENTS_TEMPLATE = "data_room/_show_protected_documents.html"


def _calculate_file_hash(file):
    """Calculate SHA256 hash of a file."""
    file.seek(0)
    sha256_hash = hashlib.sha256()
    for chunk in iter(lambda: file.read(8192), b""):
        sha256_hash.update(chunk)
    file.seek(0)
    return sha256_hash.hexdigest()


def _handle_upload(
    request, project, *, model_class, parent_kwargs, doc_type, default_type, indexing_model_name, upload_url_name
):
    user = request.user
    user_company = user.get_company()
    files = request.FILES.getlist("files")

    if not files:
        return render(
            request,
            UPLOAD_TEMPLATE,
            {
                "project": project,
                "upload_url_name": upload_url_name,
            },
        )

    created_documents = []
    upload_errors = []

    for file in files:
        try:
            file_hash = _calculate_file_hash(file)

            existing_doc = model_class.objects.filter(**parent_kwargs, file_hash=file_hash).first()
            if existing_doc:
                upload_errors.append(
                    _("%(filename)s: Already exists as '%(existing)s'")
                    % {"filename": file.name, "existing": existing_doc.name}
                )
                continue

            original_name = file.name
            file_name_base, file_ext = os.path.splitext(original_name)
            slugified_name = f"{slugify_de(file_name_base)}{file_ext.lower()}"
            file.name = slugified_name

            doc = model_class(
                name=file_name_base,
                file=file,
                **parent_kwargs,
                document_type=doc_type or default_type,
                user=user,
                user_type=user.type,
                user_company=str(user_company),
                file_hash=file_hash,
            )
            doc.save()
            created_documents.append(doc)
            logger.info(f"Created document: {doc.name} (ID: {doc.id})")

        except Exception as e:
            logger.error(f"Error creating document from {file.name}: {str(e)}")
            upload_errors.append(
                _("%(filename)s: Upload failed - %(error)s") % {"filename": file.name, "error": str(e)}
            )

    doc_ids = [doc.id for doc in created_documents]
    if doc_ids:
        try:
            task = process_document_pipeline_task.delay(doc_ids, user.id, indexing_model_name)
            logger.info(f"Indexing task sent for {len(doc_ids)} documents, task id: {task.id}")
        except Exception as e:
            logger.error(f"Error sending indexing task: {str(e)}")

    if upload_errors and not created_documents:
        return render(
            request,
            UPLOAD_TEMPLATE,
            {
                "project": project,
                "upload_url_name": upload_url_name,
                "upload_errors": upload_errors,
            },
        )

    ctx = get_document_list_context(project, user)
    if upload_errors:
        ctx["upload_errors"] = upload_errors
    return render(request, DOCUMENTS_TEMPLATE, ctx)


def _handle_single_upload(request, project, *, model_class, parent_kwargs, doc_type, default_type, indexing_model_name):
    user = request.user
    user_company = user.get_company()
    files = request.FILES.getlist("files")

    if len(files) != 1:
        return JsonResponse({"ok": False, "error": _("Expected exactly one file.")})

    file = files[0]
    try:
        file_hash = _calculate_file_hash(file)

        existing_doc = model_class.objects.filter(**parent_kwargs, file_hash=file_hash).first()
        if existing_doc:
            return JsonResponse(
                {
                    "ok": False,
                    "error": _("%(filename)s: Already exists as '%(existing)s'")
                    % {"filename": file.name, "existing": existing_doc.name},
                }
            )

        original_name = file.name
        file_name_base, file_ext = os.path.splitext(original_name)
        slugified_name = f"{slugify_de(file_name_base)}{file_ext.lower()}"
        file.name = slugified_name

        doc = model_class(
            name=file_name_base,
            file=file,
            **parent_kwargs,
            document_type=doc_type or default_type,
            user=user,
            user_type=user.type,
            user_company=str(user_company),
            file_hash=file_hash,
        )
        doc.save()
        logger.info(f"Created document: {doc.name} (ID: {doc.id})")

        try:
            task = process_document_pipeline_task.delay([doc.id], user.id, indexing_model_name)
            logger.info(f"Indexing task sent for document {doc.id}, task id: {task.id}")
        except Exception as e:
            logger.error(f"Error sending indexing task: {str(e)}")

        return JsonResponse({"ok": True, "doc_id": doc.id, "doc_name": doc.name})

    except Exception as e:
        logger.error(f"Error creating document from {file.name}: {str(e)}")
        return JsonResponse({"ok": False, "error": str(e)})


@otp_required
@project_permission_required(htmx_required=True)
def hx_upload_protected_project_document(request, pk):
    project = request.project
    if request.method == "POST":
        kwargs = dict(
            model_class=ProtectedProjectDocument,
            parent_kwargs={"project": project},
            doc_type=request.POST.get("document_type", ""),
            default_type=ProjectDocumentType.OTHER,
            indexing_model_name="ProtectedDocument",
        )
        if request.headers.get("X-Single-Upload"):
            return _handle_single_upload(request, project, **kwargs)
        return _handle_upload(request, project, **kwargs, upload_url_name="data_room:hx-upload-project")
    return render(
        request,
        UPLOAD_TEMPLATE,
        {
            "project": project,
            "upload_url_name": "data_room:hx-upload-project",
        },
    )


@otp_required
@project_permission_required(htmx_required=True)
def hx_upload_protected_client_document(request, pk):
    user = request.user
    project = request.project

    if user.id and user.type == "client" and project.client_company is None:
        project.client_company = user.get_company()
        project.save()

    client = project.client_company

    if client is None:
        return render(
            request,
            UPLOAD_TEMPLATE,
            {
                "project": project,
                "upload_url_name": "data_room:hx-upload-client",
                "upload_errors": [
                    _("No company is associated with this project yet. A client must access the project first.")
                ],
            },
        )

    if request.method == "POST":
        kwargs = dict(
            model_class=ProtectedClientDocument,
            parent_kwargs={"client": client},
            doc_type=request.POST.get("document_type", ""),
            default_type=ClientDocumentType.OTHER,
            indexing_model_name="ProtectedClientDocument",
        )
        if request.headers.get("X-Single-Upload"):
            return _handle_single_upload(request, project, **kwargs)
        return _handle_upload(request, project, **kwargs, upload_url_name="data_room:hx-upload-client")
    return render(
        request,
        UPLOAD_TEMPLATE,
        {
            "project": project,
            "upload_url_name": "data_room:hx-upload-client",
        },
    )
