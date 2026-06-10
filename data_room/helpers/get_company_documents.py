# data_room/helpers/get_company_documents.py

from django.db.models import Count

from ..models import ProjectCompanyLink, ProtectedClientDocument
from ..policies import get_policy


def get_active_company_ids(project):
    """Return list of ClientCompany PKs that are active for this project.

    Companies without a ProjectCompanyLink are considered active (default).
    Only companies with an explicit is_active=False link are excluded.
    """
    if not project.client_company:
        return []

    all_companies = list(project.client_company.get_group())
    all_ids = [c.pk for c in all_companies]

    # Get explicitly inactive links
    inactive_ids = set(
        ProjectCompanyLink.objects.filter(
            project=project,
            client_id__in=all_ids,
            is_active=False,
        ).values_list("client_id", flat=True)
    )

    return [pk for pk in all_ids if pk not in inactive_ids]


def get_company_documents(project):
    """Get ProtectedClientDocuments for project's ACTIVE client companies, grouped by client."""
    if not project.client_company:
        return []

    active_ids = get_active_company_ids(project)

    documents = (
        ProtectedClientDocument.objects.filter(client_id__in=active_ids)
        .select_related("client", "user")
        .order_by("-date_created")
    )

    grouped = {}
    for doc in documents:
        if doc.client not in grouped:
            grouped[doc.client] = []
        grouped[doc.client].append(doc)

    return sorted(grouped.items(), key=lambda x: x[0].company if x[0] else "")


def get_company_overview(project):
    """Build overview of all group companies with GuV stats and project relevance."""
    if not project.client_company:
        return [], [], None

    companies = list(project.client_company.get_group())
    company_ids = [c.pk for c in companies]

    # Document counts per client
    doc_counts = (
        ProtectedClientDocument.objects.filter(client_id__in=company_ids)
        .values("client_id")
        .annotate(count=Count("id"))
    )
    docs_by_client = {row["client_id"]: row["count"] for row in doc_counts}

    # Project company links
    links = ProjectCompanyLink.objects.filter(project=project, client_id__in=company_ids)
    links_by_client = {link.client_id: link for link in links}

    # Determine role based on holding hierarchy
    primary_client = project.client_company
    holding_ids = set()
    h = primary_client.holding
    while h:
        holding_ids.add(h.pk)
        h = h.holding

    fiscal_years = []

    overview = []
    for company in companies:
        link = links_by_client.get(company.pk)
        is_active = link.is_active if link else True  # default active

        # Determine role
        if company.pk == primary_client.pk:
            role = "Leasingnehmer"
        elif company.pk in holding_ids:
            role = "Holding"
        elif company.holding_id == primary_client.holding_id and company.holding_id is not None:
            role = "Schwester"
        elif company.holding_id == primary_client.pk:
            role = "Tochter"
        else:
            role = "Gruppe"

        overview.append(
            {
                "client": company,
                "link": link,
                "is_active": is_active,
                "role": role,
                "doc_count": docs_by_client.get(company.pk, 0),
            }
        )

    # Sort: Leasingnehmer first, then by company name
    role_order = {"Leasingnehmer": 0, "Holding": 1, "Tochter": 2, "Schwester": 3, "Gruppe": 4}
    overview.sort(key=lambda x: (role_order.get(x["role"], 9), x["client"].company))

    # Generate orgchart SVG
    from data_room.helpers.orgchart import build_orgchart_dot, render_orgchart_svg

    dot_str = build_orgchart_dot(project.client_company)
    orgchart_svg = render_orgchart_svg(dot_str) if dot_str else None

    return overview, fiscal_years, orgchart_svg


def is_not_partner(user):
    """Mirror the is_not_partner template tag logic for use in view context."""
    if user and user.pk and user.is_verified():
        return user.type != "partner"
    return True


def get_document_list_context(project, user):
    """Build context dict for the document list template.

    Unlike hx_empty this list keeps drafts visible (restrict_drafts=False);
    the partner author scope still applies via the policy."""
    policy = get_policy()
    documents = project.protected_documents.all()
    buckets = policy.filter_project_document_buckets(
        user,
        project,
        {
            "client": documents.filter(user_type="client"),
            "broker": documents.filter(user_type="broker"),
            "partner": documents.filter(user_type="partner"),
        },
        restrict_drafts=False,
    )

    company_overview, fiscal_years, orgchart_svg = get_company_overview(project)

    return {
        "project": project,
        "client_documents": buckets["client"],
        "broker_documents": buckets["broker"],
        "partner_documents": buckets["partner"],
        "company_documents": get_company_documents(project),
        "company_overview": company_overview,
        "fiscal_years": fiscal_years,
        "orgchart_svg": orgchart_svg,
        "not_partner": policy.can_view_company_documents(user, project.client_company),
    }
