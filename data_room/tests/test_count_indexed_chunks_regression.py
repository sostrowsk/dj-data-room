"""Regression test: count_indexed_chunks_task called scribe.count_document_chunks(),
a method that never existed — the per-document exception was swallowed and chunk
counts were never updated. The task must count via the DocumentChunk ORM (plan
Phase A6)."""

from django.test import TestCase
from scribe.tests.factories import DocumentChunkFactory

from data_room.tasks.index_document import count_indexed_chunks_task
from data_room.tests.factories import ProtectedDocumentFactory
from users.factories import create_admin


class CountIndexedChunksRegressionTests(TestCase):
    def test_count_indexed_chunks_task_syncs_counts_from_postgres_chunks(self):
        admin = create_admin()
        document = ProtectedDocumentFactory(indexed_chunks=99)
        project = document.project
        collection_name = f"project_{project.id}"
        for i in range(3):
            DocumentChunkFactory(
                collection_name=collection_name,
                document_id=document.id,
                project_id=project.id,
                chunk_id=i,
            )

        result = count_indexed_chunks_task.apply(args=[admin.id]).result

        document.refresh_from_db()
        project.refresh_from_db()
        self.assertEqual(document.indexed_chunks, 3)
        self.assertEqual(project.indexed_chunks, 3)
        self.assertEqual(result["documents_updated"], 1)
        self.assertEqual(result["projects_updated"], 1)

    def test_count_indexed_chunks_task_skips_projects_without_chunks(self):
        """A7: projects are discovered via distinct project_ids from the chunk
        table instead of Project.objects.all() — chunkless projects are not
        visited (and their counts not touched)."""
        admin = create_admin()
        document = ProtectedDocumentFactory(indexed_chunks=0)
        project = document.project
        project.indexed_chunks = 7
        project.save()

        result = count_indexed_chunks_task.apply(args=[admin.id]).result

        project.refresh_from_db()
        self.assertEqual(project.indexed_chunks, 7)
        self.assertEqual(result["projects_processed"], 0)
        self.assertEqual(result["projects_updated"], 0)

    def test_count_indexed_chunks_task_leaves_correct_counts_untouched(self):
        admin = create_admin()
        document = ProtectedDocumentFactory(indexed_chunks=2)
        project = document.project
        project.indexed_chunks = 2
        project.save()
        collection_name = f"project_{project.id}"
        for i in range(2):
            DocumentChunkFactory(
                collection_name=collection_name,
                document_id=document.id,
                project_id=project.id,
                chunk_id=i,
            )

        result = count_indexed_chunks_task.apply(args=[admin.id]).result

        self.assertEqual(result["documents_updated"], 0)
        self.assertEqual(result["projects_updated"], 0)
