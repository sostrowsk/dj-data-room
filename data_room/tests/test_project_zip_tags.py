from django.template import Context, Template
from django.test import TestCase, override_settings
from progress.models import TaskProgress

from data_room.models import ProjectZip
from project.tests.factories import ProjectFactory
from users.tests.factories import ClientFactory


@override_settings(LANGUAGE_CODE="de")
class ProjectZipTagsTests(TestCase):
    def setUp(self):
        self.user = ClientFactory()
        self.project = ProjectFactory(client_company=self.user.client_company)

    def test_get_active_zip_task_no_task(self):
        template = Template("{% load project_zip_tags %}{{ project|get_active_zip_task:user }}")
        context = Context({"project": self.project, "user": self.user})
        result = template.render(context)
        self.assertEqual(result, "None")

    def test_get_active_zip_task_with_task(self):
        TaskProgress.objects.create(
            user=self.user,
            task_type="Download ZIP generation",
            task_object_id=str(self.project.id),
            status="running",
            progress=50.0,
        )

        template = Template("{% load project_zip_tags %}{{ project|get_active_zip_task:user }}")
        context = Context({"project": self.project, "user": self.user})
        result = template.render(context)
        # TaskProgress.__str__ returns "Task Download ZIP generation (running): 50.0%"
        self.assertIn("Download ZIP generation", result)
        self.assertIn("running", result)
        self.assertIn("50.0%", result)

    def test_get_latest_zip_no_zip(self):
        template = Template("{% load project_zip_tags %}{{ project|get_latest_zip:user }}")
        context = Context({"project": self.project, "user": self.user})
        result = template.render(context)
        self.assertEqual(result, "None")

    def test_get_latest_zip_with_zip(self):
        ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="completed",
        )
        ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="completed",
        )

        template = Template("{% load project_zip_tags %}{{ project|get_latest_zip:user }}")
        context = Context({"project": self.project, "user": self.user})
        result = template.render(context)
        # ProjectZip.__str__ returns "ZIP for {project.name} ({date})"
        self.assertIn("ZIP for", result)
        self.assertIn(self.project.name, result)

    def test_get_latest_zip_ignores_processing(self):
        ProjectZip.objects.create(
            project=self.project,
            user=self.user,
            status="processing",
        )

        template = Template("{% load project_zip_tags %}{{ project|get_latest_zip:user }}")
        context = Context({"project": self.project, "user": self.user})
        result = template.render(context)
        self.assertEqual(result, "None")
