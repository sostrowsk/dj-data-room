from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django_otp.decorators import otp_required

from ...conf import get_login_url, get_project_model
from ...policies import get_policy

Project = get_project_model()


@otp_required
def hx_project_zip_button(request, pk):
    user = request.user
    if not user.is_authenticated:
        login_url = f"{get_login_url()}?next={request.path}"
        return render(
            request,
            "data_room/_login_required.html",
            {"login_url": login_url},
            status=401,
        )

    project = get_object_or_404(Project, pk=pk)
    if not get_policy().can_access_project(user, project):
        raise PermissionDenied

    return render(
        request,
        "data_room/_zip_button.html",
        {
            "project": project,
            "user": user,
        },
    )
