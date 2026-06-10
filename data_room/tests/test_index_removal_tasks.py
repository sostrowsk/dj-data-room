"""Phase A6: removal tasks delegate to the SCRIBE facade (Postgres-SSOT +
best-effort Milvus) instead of poking langchain vector-store internals."""

from unittest.mock import AsyncMock, patch

from django.test import TestCase
from progress.services import ProgressTrackerService

from data_room.tasks.delete_collection_from_index import delete_collection_from_index_sync
from data_room.tasks.remove_document_from_index import remove_document_from_index_sync
from project.tests.project_utils import create_project
from users.factories import create_admin


class RemoveDocumentFromIndexTests(TestCase):
    def test_remove_document_calls_scribe_delete_documents(self):
        admin = create_admin()
        progress = ProgressTrackerService.create_task(
            user=admin, task_type="Document Removal", total_steps=4, task_object_id="42"
        )
        with patch("data_room.tasks.remove_document_from_index.SCRIBE") as mock_scribe_cls:
            scribe = mock_scribe_cls.return_value
            scribe.delete_documents = AsyncMock()

            result = remove_document_from_index_sync(42, progress.id, "project_7")

        mock_scribe_cls.assert_called_once_with("project_7")
        scribe.delete_documents.assert_awaited_once_with(document_id=42)
        self.assertEqual(result["status"], "success")


class DeleteCollectionFromIndexTests(TestCase):
    def test_delete_collection_calls_scribe_drop_collection(self):
        admin = create_admin()
        project = create_project()
        progress = ProgressTrackerService.create_task(
            user=admin, task_type="Collection Deletion", total_steps=3, task_object_id=str(project.id)
        )
        with patch("data_room.tasks.delete_collection_from_index.SCRIBE") as mock_scribe_cls:
            scribe = mock_scribe_cls.return_value
            scribe.drop_collection = AsyncMock(return_value=True)

            result = delete_collection_from_index_sync(project.id, progress.id)

        mock_scribe_cls.assert_called_once_with(f"project_{project.id}")
        scribe.drop_collection.assert_awaited_once()
        self.assertEqual(result["status"], "success")
