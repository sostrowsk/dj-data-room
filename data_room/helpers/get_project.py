# data_room/helpers/get_project.py
from django.db import transaction

from data_room.conf import get_project_model

Project = get_project_model()


def get_project(project_id):
    with transaction.atomic():
        return Project.objects.select_for_update().get(id=project_id)
