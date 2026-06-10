# data_room/views/hx/empty.py
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django_otp.decorators import otp_required

from ...conf import get_login_url, get_project_model
from ...helpers import get_company_documents
from ...policies import get_policy

Project = get_project_model()


@otp_required
def hx_empty(request, pk):
    if not request.htmx:
        raise PermissionDenied
    project = get_object_or_404(Project, pk=pk)
    user = request.user
    policy = get_policy()
    if not policy.can_access_project(user, project):
        if not user.is_authenticated:
            login_url = f"{get_login_url()}?next={request.path}"
            return redirect(login_url)
        else:
            raise PermissionDenied
    documents = project.protected_documents.all()

    # Apply sorting based on request parameter
    sort_by = request.GET.get("sort_documents", "name")
    if sort_by == "name":
        documents = documents.order_by("name")
    elif sort_by == "-name":
        documents = documents.order_by("-name")
    elif sort_by == "date":
        documents = documents.order_by("date_created")
    elif sort_by == "-date":
        documents = documents.order_by("-date_created")
    else:
        documents = documents.order_by("name")

    buckets = policy.filter_project_document_buckets(
        user,
        project,
        {
            "client": documents.filter(user_type="client"),
            "broker": documents.filter(user_type="broker"),
            "partner": documents.filter(user_type="partner"),
        },
    )

    return render(
        request,
        "data_room/_show_protected_documents.html",
        {
            "project": project,
            "client_documents": buckets["client"],
            "broker_documents": buckets["broker"],
            "partner_documents": buckets["partner"],
            "company_documents": get_company_documents(project),
            "not_partner": policy.can_view_company_documents(user, project.client_company),
        },
    )
