# Add to data_room/views/api/project_zip.py
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_otp.decorators import otp_required
from progress.models import TaskProgress

from data_room.conf import get_login_url, get_project_detail_url, get_project_model
from data_room.models.project_zip import ProjectZip
from data_room.policies import get_policy
from data_room.tasks.project_zip import project_zip_task

Project = get_project_model()


@otp_required
def project_zip_progress_view(request, pk):
    user = request.user
    if not user.is_authenticated:
        login_url = f"{get_login_url()}?next={request.path}"
        return redirect(login_url)

    project = get_object_or_404(Project, pk=pk)
    if not get_policy().can_access_project(user, project):
        raise PermissionDenied

    # Get active task for this project
    active_task = (
        TaskProgress.objects.filter(
            user=user,
            task_type="Download ZIP generation",
            task_object_id=str(project.id),
            status__in=["pending", "running"],
        )
        .order_by("-date_created")
        .first()
    )

    # Get completed ZIPs for this project
    completed_zips = ProjectZip.objects.filter(project=project, user=user, status="completed").order_by("-date_created")

    context = {
        "project": project,
        "active_task": active_task,
        "completed_zips": completed_zips,
    }

    return render(request, "data_room/project_zip_progress.html", context)


@otp_required
def start_project_zip_generation(request, pk):
    user = request.user
    if not user.is_authenticated:
        login_url = f"{get_login_url()}?next={request.path}"
        return redirect(login_url)

    project = get_object_or_404(Project, pk=pk)
    if not get_policy().can_access_project(user, project):
        raise PermissionDenied

    # Import TaskProgress here to avoid circular imports
    from progress.models import TaskProgress

    # Check if there's already an active task
    active_task = TaskProgress.objects.filter(
        user=user,
        task_type="Download ZIP generation",
        task_object_id=str(project.id),
        status__in=["pending", "running"],
    ).exists()

    if active_task:
        messages.info(request, "A ZIP file generation is already in progress.")
    else:
        # Delete any old completed ZIPs before starting new one
        old_zips = ProjectZip.objects.filter(project=project, user=user)
        for old_zip in old_zips:
            if old_zip.zip_file:
                old_zip.zip_file.delete()
            old_zip.delete()

        project_zip_task.delay(project.id, user.id)
        messages.success(
            request,
            "ZIP file generation started. You will be notified when it's ready.",
        )

    # Get the referring URL to redirect back to the same page
    referer = request.META.get("HTTP_REFERER")
    if referer and referer.startswith(request.build_absolute_uri("/")):
        # If we have a valid referer from our site, go back to it
        return redirect(referer)
    else:
        # Otherwise, go to the project detail page with data room section
        return redirect(f"{reverse(get_project_detail_url(), kwargs={'pk': project.pk})}#data-room-section")
