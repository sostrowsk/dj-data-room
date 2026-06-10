"""Phase A6: document_chunks_view reads chunks from the DocumentChunk ORM
(Postgres-SSOT) instead of querying Milvus through SCRIBE internals."""

from django.test import TestCase
from django.urls import reverse
from scribe.tests.factories import DocumentChunkFactory

from data_room.tests.factories import ProtectedDocumentFactory
from users.factories import create_admin, login_and_verify


class DocumentChunksViewTests(TestCase):
    def setUp(self):
        self.admin = create_admin()
        self.document = ProtectedDocumentFactory()
        self.collection_name = f"project_{self.document.project_id}"

    def test_view_renders_chunks_from_postgres(self):
        DocumentChunkFactory(
            collection_name=self.collection_name,
            document_id=self.document.id,
            project_id=self.document.project_id,
            chunk_id=0,
            content="**Maschinenliste** mit Leasingraten",
        )
        DocumentChunkFactory(
            collection_name=self.collection_name,
            document_id=self.document.id,
            project_id=self.document.project_id,
            chunk_id=1,
            content="Zweiter Chunk Inhalt",
        )
        login_and_verify(self.admin, self.client)

        response = self.client.get(reverse("data_room:document_chunks", kwargs={"pk": self.document.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Maschinenliste")
        self.assertContains(response, "Zweiter Chunk Inhalt")

    def test_view_ignores_chunks_of_other_documents(self):
        other_document = ProtectedDocumentFactory()
        DocumentChunkFactory(
            collection_name=f"project_{other_document.project_id}",
            document_id=other_document.id,
            project_id=other_document.project_id,
            chunk_id=0,
            content="Fremder Chunk darf nicht erscheinen",
        )
        login_and_verify(self.admin, self.client)

        response = self.client.get(reverse("data_room:document_chunks", kwargs={"pk": self.document.id}))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Fremder Chunk")
