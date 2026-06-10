# data_room/templatetags/project_zip_tags.py
from django import template
from progress.models import TaskProgress

from data_room.models.project_zip import ProjectZip

register = template.Library()


@register.filter
def get_active_zip_task(project, user):
    if user.is_anonymous:
        return None

    return (
        TaskProgress.objects.filter(
            user=user,
            task_type="Download ZIP generation",
            task_object_id=str(project.id),
            status__in=["pending", "running"],
        )
        .order_by("-date_created")
        .first()
    )


@register.filter
def get_latest_zip(project, user):
    if user.is_anonymous:
        return None

    return ProjectZip.objects.filter(project=project, user=user, status="completed").order_by("-date_created").first()


@register.inclusion_tag("data_room/project_zip_button.html")
def render_project_zip_button(project, user):
    active_task = get_active_zip_task(project, user)
    latest_zip = get_latest_zip(project, user)

    return {
        "project": project,
        "user": user,
        "active_task": active_task,
        "latest_zip": latest_zip,
    }
