"""Regression tests for codex review round 2 findings.

Addresses:
- P1: `process_pending_documents_task` must route through the full pipeline,
      not just index_queried_documents_task (which skips LLM extraction).
- P2: classifier metadata (document_type, fiscal_year, statement_type) must
      be persisted even when client extraction returned no entities.
- P2: concurrent _process_single_document_task runs on the same document
      must be rejected (not run in parallel, racing Milvus writes).
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.files.base import ContentFile


@pytest.fixture
def user(db):
    from users.factories import create_broker

    return create_broker()


@pytest.fixture
def client_doc(db, user):
    from data_room.models import ProtectedClientDocument
    from users.models import ClientCompany

    client = ClientCompany.objects.create(company="TestCo", register_number="HRB 1", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=client,
        name="doc",
        user=user,
        user_type="broker",
        user_company="B",
        indexing_status="pending",
    )
    doc.file.save("x.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestProcessPendingUsesFullPipeline:
    """P1: Pending docs must re-run the full pipeline (extraction + indexing)."""

    def test_pending_client_docs_routed_through_full_pipeline(self, client_doc):
        from data_room.tasks.index_document import process_pending_documents_task

        # process_document_pipeline_task must be called
        with patch("data_room.tasks.index_document.process_document_pipeline_task") as mock_pipeline, patch(
            "data_room.tasks.index_document.index_queried_documents_task"
        ) as mock_index_only:
            process_pending_documents_task()

            # Full pipeline was invoked
            called_with_client = any(
                call.args and "ProtectedClientDocument" in str(call) for call in mock_pipeline.delay.call_args_list
            )
            assert (
                called_with_client or mock_pipeline.delay.call_count > 0
            ), "process_document_pipeline_task should be called for pending client docs"

            # index-only path must NOT be used for retry
            assert not mock_index_only.delay.called, "Retry must not bypass extraction via index_queried_documents_task"


@pytest.mark.django_db
class TestClassifierMetadataPersisted:
    """P2: document_type/fiscal_year/statement_type must persist even without entities."""

    def test_metadata_saved_when_no_entities_returned(self, client_doc, user):
        from ai_agents.schemas.client import ClientExtractionResult
        from ai_agents.tasks.extract_document_data import extract_document_data_task

        # Classifier returned doc_type but NO entities (e.g. rating, structure chart)
        empty_entities_result = ClientExtractionResult(
            entities=[],
            document_type="rating",
            fiscal_year=2024,
            statement_type="single",
        )

        # Mock the LLM call to return this result for the primer + empty for others.
        # **kwargs absorbs the Phase-5 `pdf_bytes` kwarg added to _run_extraction_call.
        def fake_extraction(client, pdf_path, system_prompt, extraction_prompt, output_schema, label, **kwargs):
            result_meta = MagicMock(input_tokens=10, cache_read_input_tokens=0, cache_creation_input_tokens=10)
            if label == "client":
                return empty_entities_result, result_meta
            if label == "markdown":
                result_meta.content = "# markdown"
                return None, result_meta
            return None, result_meta

        with patch("ai_agents.tasks.extract_document_data._run_extraction_call", side_effect=fake_extraction), patch(
            "ai_agents.tasks.extract_document_data._get_cached_client_and_model",
            return_value=(MagicMock(), "test-model"),
        ):
            extract_document_data_task.apply(
                args=[client_doc.id],
                kwargs={"user_id": user.id, "document_model": "ProtectedClientDocument"},
            )

        client_doc.refresh_from_db()
        assert client_doc.document_type == "rating"
        assert client_doc.fiscal_year == 2024
        assert client_doc.statement_type == "single"


@pytest.mark.django_db
class TestConcurrentPipelineRejected:
    """P2: A second pipeline run for an already-processing doc must not race."""

    def test_already_processing_doc_is_rejected(self, client_doc, user):
        """Doc already in processing/chunking/indexing should not start again."""
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task

        # Simulate already running pipeline
        ProtectedClientDocument.objects.filter(id=client_doc.id).update(indexing_status="chunking")

        with patch("ai_agents.tasks.extract_document_data.extract_document_data_task.apply") as mock_extract, patch(
            "data_room.tasks.index_document.index_document_task.apply"
        ) as mock_index:
            _process_single_document_task(client_doc.id, user.id, "ProtectedClientDocument")

            mock_extract.assert_not_called()
            mock_index.assert_not_called()
