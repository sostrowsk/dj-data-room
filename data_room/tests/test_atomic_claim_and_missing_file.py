"""Regression tests for codex review round 6 findings.

P1: The concurrency guard checks indexing_status then unconditionally
    updates to `processing` — two workers can race through the check.
    Must use atomic conditional UPDATE.
P2: Missing-file rows loop forever: pipeline fails → reset to pending →
    beat requeues. Must mark as `failed` (terminal) when file missing.
"""

from unittest.mock import patch

import pytest
from django.core.files.base import ContentFile


@pytest.fixture
def user(db):
    from users.factories import create_broker

    return create_broker()


@pytest.fixture
def queued_client_doc(db, user):
    from data_room.models import ProtectedClientDocument
    from users.models import ClientCompany

    cc = ClientCompany.objects.create(company="TC", register_number="HRB 1", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=cc,
        name="d",
        user=user,
        user_type="broker",
        user_company="B",
        indexing_status="queued",
    )
    doc.file.save("f.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestAtomicClaim:
    """P1: Concurrency guard must claim the doc atomically."""

    def test_second_worker_blocked_after_first_claimed(self, queued_client_doc, user):
        """Simulate: Worker A claims, then Worker B tries — B must not run."""
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task

        # Worker A claimed and set to processing
        ProtectedClientDocument.objects.filter(id=queued_client_doc.id).update(indexing_status="processing")

        with patch("ai_agents.tasks.extract_document_data.extract_document_data_task.apply") as mock_extract, patch(
            "data_room.tasks.index_document.index_document_task.apply"
        ) as mock_index:
            _process_single_document_task(queued_client_doc.id, user.id, "ProtectedClientDocument")

            mock_extract.assert_not_called()
            mock_index.assert_not_called()

    def test_only_one_of_two_concurrent_workers_succeeds_to_claim(self, queued_client_doc, user):
        """Test the atomic claim: only 1 of 2 workers should proceed when status=queued."""
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _claim_document

        # Worker A claims
        claim_a = _claim_document(ProtectedClientDocument, queued_client_doc.id)
        assert claim_a is True, "First worker must successfully claim"

        # Worker B tries to claim same doc
        claim_b = _claim_document(ProtectedClientDocument, queued_client_doc.id)
        assert claim_b is False, "Second worker must be rejected"


@pytest.mark.django_db
class TestMissingFileMarkedFailed:
    """P2: Docs without accessible files must be marked failed, not retried forever."""

    def test_doc_with_empty_file_field_marked_failed(self, db, user):
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task
        from users.models import ClientCompany

        cc = ClientCompany.objects.create(company="TC2", register_number="HRB 2", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=cc,
            name="no-file",
            user=user,
            user_type="broker",
            user_company="B",
            indexing_status="pending",
        )
        # No file saved — file field is empty

        _process_single_document_task(doc.id, user.id, "ProtectedClientDocument")

        doc.refresh_from_db()
        # Must be terminal-failed, NOT pending (which would cause infinite retry)
        assert (
            doc.indexing_status == "failed"
        ), f"Expected 'failed' (terminal), got '{doc.indexing_status}' — would loop forever"
