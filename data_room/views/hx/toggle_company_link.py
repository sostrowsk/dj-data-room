from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from data_room.conf import get_project_model
from data_room.helpers.get_company_documents import get_company_overview
from data_room.models import ProjectCompanyLink

Project = get_project_model()


@login_required
@require_POST
def hx_toggle_company_link(request, project_pk, client_pk):
    """Toggle is_active for a ProjectCompanyLink (HTMX)."""
    project = get_object_or_404(Project, pk=project_pk)

    link, _created = ProjectCompanyLink.objects.get_or_create(
        project=project,
        client_id=client_pk,
        defaults={"is_active": False},  # toggling from default-active → create as inactive
    )
    if not _created:
        link.is_active = not link.is_active
        link.save(update_fields=["is_active"])

    # Re-render the full overview table
    company_overview, fiscal_years, orgchart_svg = get_company_overview(project)
    html = render_to_string(
        "data_room/_company_overview.html",
        {
            "project": project,
            "company_overview": company_overview,
            "fiscal_years": fiscal_years,
            "orgchart_svg": orgchart_svg,
        },
        request=request,
    )
    return HttpResponse(html)
