import logging
import mimetypes
import os

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.static import serve
from django_otp.decorators import otp_required

from ..conf import get_login_url, get_project_detail_url
from ..decorators import project_permission_required, protected_document_permission_required
from ..forms import UploadProtectedDocumentForm
from ..models import ProtectedClientDocument, ProtectedProjectDocument
from ..policies import get_policy
from ..tasks import delete_collection_from_index_task, remove_document_from_index_task

logger = logging.getLogger(__name__)

# Template mappings for HTMX responses
CLIENT_DOC_TEMPLATES = {
    "card": "data_room/_show_company_document_card.html",
    "table": "data_room/_show_company_document_table_row.html",
}

PROJECT_DOC_TEMPLATES = {
    "card": "data_room/_show_protected_document.html",
    "table": "data_room/_show_project_document_table_row.html",
}

DEFAULT_X_SENDFILE_HEADER = "X-Sendfile"

_SERVER_HEADER_MAP = {
    "nginx": "X-Accel-Redirect",
}


def _require_auth_or_redirect(request):
    """Return redirect response if not authenticated, else None."""
    if not request.user.is_authenticated:
        login_url = f"{get_login_url()}?next={request.path}"
        return redirect(login_url)
    return None


def _htmx_response_or_redirect(request, document, templates, extra_context=None):
    """Return HTMX response with correct template or redirect to referrer."""
    if request.headers.get("HX-Request"):
        view_type = request.GET.get("view", "card")
        template = templates.get(view_type, templates["card"])
        context = {"protected_document": document, "user": request.user}
        if extra_context:
            context.update(extra_context)
        return render(request, template, context)
    return redirect(request.META.get("HTTP_REFERER", "/"))


def _dispatch_index_removal(document_id, user_id, collection_name=None):
    """Dispatch task to remove document from index."""
    try:
        kwargs = {}
        if collection_name:
            kwargs["collection_name"] = collection_name
        task = remove_document_from_index_task.delay(document_id, user_id, **kwargs)
        logger.info(f"Index removal task: id={task.id}, status={task.status}")
    except Exception as e:
        logger.error(f"Index removal failed: {e}")


def _check_project_document_permission(user, protected_document):
    """Check if user has permission to manage a project document."""
    return get_policy().can_manage_project_document(user, protected_document)


@otp_required
def protected_document_view(request, path, server=None, as_download=None):
    user = request.user
    if not user.is_authenticated:
        login_url = f"{get_login_url()}?next={request.path}"
        return redirect(login_url)
    if not user.is_superuser:
        raise PermissionDenied

    if server is None:
        server = settings.PROTECTED_MEDIA_SERVER
    if as_download is None:
        as_download = settings.PROTECTED_MEDIA_AS_DOWNLOADS

    full_path = os.path.join(settings.PROTECTED_MEDIA_ROOT, path)
    if not os.path.exists(full_path):
        raise Http404("File does not exist")
    if server != "django":
        mimetype, encoding = mimetypes.guess_type(path)
        response = HttpResponse()
        response["Content-Type"] = mimetype
        if encoding:
            response["Content-Encoding"] = encoding
        if as_download:
            response["Content-Disposition"] = f"attachment; filename={os.path.basename(path)}"
        else:
            response["Content-Disposition"] = f"inline; filename={os.path.basename(path)}"

        server_header_value = _SERVER_HEADER_MAP.get(server, DEFAULT_X_SENDFILE_HEADER)
        response[server_header_value] = os.path.join(settings.PROTECTED_MEDIA_LOCATION_PREFIX, path).encode("utf8")
        return response
    else:
        return serve(
            request,
            path,
            document_root=settings.PROTECTED_MEDIA_ROOT,
            show_indexes=False,
        )


def protected_document_reviewed(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if _check_project_document_permission(request.user, protected_document):
        protected_document.reviewed = True
        protected_document.use_ai = True
        protected_document.save()

    return _htmx_response_or_redirect(
        request, protected_document, PROJECT_DOC_TEMPLATES, {"project": protected_document.project}
    )


def protected_document_disabled(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if _check_project_document_permission(request.user, protected_document):
        protected_document.disabled = True
        protected_document.use_ai = False
        protected_document.save()
        _dispatch_index_removal(protected_document.id, request.user.id)

    return _htmx_response_or_redirect(
        request, protected_document, PROJECT_DOC_TEMPLATES, {"project": protected_document.project}
    )


def protected_document_enable(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if _check_project_document_permission(request.user, protected_document):
        protected_document.disabled = False
        protected_document.save()

    return _htmx_response_or_redirect(
        request, protected_document, PROJECT_DOC_TEMPLATES, {"project": protected_document.project}
    )


def toggle_ai(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if _check_project_document_permission(request.user, protected_document):
        protected_document.use_ai = not protected_document.use_ai
        protected_document.save()

    return _htmx_response_or_redirect(
        request, protected_document, PROJECT_DOC_TEMPLATES, {"project": protected_document.project}
    )


def protected_document_delete(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if _check_project_document_permission(request.user, protected_document):
        doc_id = protected_document.id
        collection_name = f"project_{protected_document.project_id}"
        protected_document.delete()
        _dispatch_index_removal(doc_id, request.user.id, collection_name=collection_name)

    if request.headers.get("HX-Request"):
        return HttpResponse("")

    referrer = request.META.get("HTTP_REFERER", "/") + "#data-room-section"
    return redirect(referrer)


@project_permission_required
def protected_document_delete_all(request, pk):
    user = request.user
    project = request.project
    if get_policy().can_manage_project_documents(user, project):
        project.protected_documents.all().delete()
        try:
            task = delete_collection_from_index_task.delay(project.id, user.id)
            logger.info(f"Task sent with id: {task.id}, status: {task.status}")
        except Exception as e:
            logger.error(f"Error sending task: {str(e)}")
    referrer = request.META.get("HTTP_REFERER", "/") + "#data-room-section"
    return redirect(referrer)


@protected_document_permission_required
def detail_view(request, pk):
    protected_document = request.protected_document
    context = {
        "protected_document": protected_document,
    }
    return render(request, "data_room/protected_document_detail.html", context)


@protected_document_permission_required
def redirect_view(request, pk):
    protected_document = request.protected_document
    if protected_document.disabled:
        raise PermissionDenied
    # if protected_document.link:
    #     return redirect(protected_document.link)
    return redirect(reverse("data_room:detail", args=(protected_document.id,)))


@project_permission_required
def create_view(request, pk):
    user = request.user
    project = request.project

    if request.method == "POST":
        form = UploadProtectedDocumentForm(request.POST, request.FILES, project=project)
        if form.is_valid():
            document = form.save(commit=False)
            document.project = project
            document.user = user
            document.user_type = user.type
            document.save()
            return redirect(reverse(get_project_detail_url(), kwargs={"pk": project.pk}))
    else:
        form = UploadProtectedDocumentForm()

    context = {
        "form": form,
        "project": project,
    }
    return render(request, "data_room/upload_protected_document.html", context)


# Client Document Views
def _check_write_permission(user, client_document):
    """Check if user has permission to MANAGE a client document (write/destructive)."""
    return get_policy().can_manage_client_document(user, client_document)


def _check_client_document_original_permission(user, client_document):
    """Raw (unwatermarked) originals: gated via the permission policy."""
    return get_policy().can_download_original(user, client_document)


def client_document_reviewed(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if _check_write_permission(request.user, client_document):
        client_document.reviewed = True
        client_document.save()

    return _htmx_response_or_redirect(request, client_document, CLIENT_DOC_TEMPLATES)


def client_document_disabled(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if _check_write_permission(request.user, client_document):
        client_document.disabled = True
        client_document.use_ai = False
        client_document.save()
        _dispatch_index_removal(
            client_document.id, request.user.id, collection_name=f"client_{client_document.client_id}"
        )

    return _htmx_response_or_redirect(request, client_document, CLIENT_DOC_TEMPLATES)


def client_document_enable(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if _check_write_permission(request.user, client_document):
        client_document.disabled = False
        client_document.save()

    return _htmx_response_or_redirect(request, client_document, CLIENT_DOC_TEMPLATES)


def client_document_delete(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if _check_write_permission(request.user, client_document):
        doc_id = client_document.id
        collection_name = f"client_{client_document.client_id}"
        client_document.delete()
        _dispatch_index_removal(doc_id, request.user.id, collection_name=collection_name)

    if request.headers.get("HX-Request"):
        return HttpResponse("")

    referrer = request.META.get("HTTP_REFERER", "/") + "#data-room-section"
    return redirect(referrer)


def client_document_toggle_ai(request, pk):
    if auth_redirect := _require_auth_or_redirect(request):
        return auth_redirect

    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if _check_write_permission(request.user, client_document):
        client_document.use_ai = not client_document.use_ai
        client_document.save()

    return _htmx_response_or_redirect(request, client_document, CLIENT_DOC_TEMPLATES)


@otp_required
def client_document_pdf(request, pk):
    """Download client document PDF (with watermark)."""
    from data_room.views.api.protected_document import DocumentProcessor, create_download_response

    user = request.user
    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if not client_document.check_permissions(user):
        raise PermissionDenied
    if not client_document.file:
        raise Http404("File not found")

    processor = DocumentProcessor(user.email)
    pdf_bytes = processor.generate_pdf(client_document)
    base_filename = os.path.basename(client_document.file.path)
    filename = os.path.splitext(base_filename)[0] + ".pdf"
    return create_download_response(pdf_bytes, filename, "application/pdf")


@otp_required
def client_document_original(request, pk):
    """Download original client document (unwatermarked — privileged + scoped)."""
    user = request.user
    client_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if not _check_client_document_original_permission(user, client_document):
        raise PermissionDenied
    # Try original first, fall back to file
    file_field = client_document.original if client_document.original else client_document.file
    if file_field:
        file_path = file_field.path
        if os.path.exists(file_path):
            content_type, _ = mimetypes.guess_type(file_path)
            with open(file_path, "rb") as f:
                response = HttpResponse(f.read(), content_type=content_type or "application/octet-stream")
                response["Content-Disposition"] = f'attachment; filename="{os.path.basename(file_path)}"'
                return response
    raise Http404("File not found")
