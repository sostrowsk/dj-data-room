"""Regression tests for DocumentIndexer._emergency_status_update.

Bug staging: ProtectedClientDocument#105 indexing_attempts=7336.
The emergency-update SQL increments unconditionally (`+ 1` no WHERE
guard), so every Celery retry adds another increment even after the
doc is on a terminal status. Result: counter blows up, no terminal
state, log floods.

Fix:
- WHERE filter excludes terminal statuses ('indexed', 'failed') so
  date_updated doesn't churn either.
- LEAST(indexing_attempts + 1, MAX_ATTEMPTS) caps the counter.
- When attempts >= MAX_ATTEMPTS, status is forced to 'failed' regardless
  of the requested status (defensive — caller may pass 'failed' anyway).
"""

from unittest.mock import MagicMock

import pytest

from data_room.helpers.update_document_status import MAX_ATTEMPTS
from data_room.tasks.index_document import DocumentIndexer
from data_room.tests.factories import ProtectedDocumentFactory


def _make_indexer_for(doc) -> DocumentIndexer:
    indexer = DocumentIndexer.__new__(DocumentIndexer)
    indexer.protected_document = doc
    indexer.model_name = type(doc).__name__
    indexer.progress = None
    indexer.use_pipeline = False
    indexer.task_id = "test-task"
    indexer.user = MagicMock()
    indexer.user.id = 1
    return indexer


@pytest.mark.django_db
class TestEmergencyStatusUpdateGuards:
    def test_does_not_increment_terminal_failed_status(self):
        doc = ProtectedDocumentFactory.create(indexing_status="failed", indexing_attempts=2)
        indexer = _make_indexer_for(doc)
        original_updated = doc.date_updated

        indexer._emergency_status_update("failed")

        doc.refresh_from_db()
        # Counter must NOT bump — already terminal.
        assert doc.indexing_attempts == 2
        # date_updated must not churn either (WHERE filter excludes terminal).
        assert doc.date_updated == original_updated

    def test_does_not_increment_terminal_indexed_status(self):
        doc = ProtectedDocumentFactory.create(indexing_status="indexed", indexing_attempts=1)
        indexer = _make_indexer_for(doc)
        original_updated = doc.date_updated

        indexer._emergency_status_update("failed")

        doc.refresh_from_db()
        assert doc.indexing_attempts == 1
        assert doc.date_updated == original_updated

    def test_caps_at_max_attempts_and_sets_failed(self):
        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=MAX_ATTEMPTS - 1)
        indexer = _make_indexer_for(doc)

        indexer._emergency_status_update("failed")

        doc.refresh_from_db()
        # Counter caps at MAX_ATTEMPTS — no overshoot like 7336.
        assert doc.indexing_attempts == MAX_ATTEMPTS
        assert doc.indexing_status == "failed"

    def test_repeated_emergency_calls_dont_overshoot_max(self):
        """The original bug: each retry adds +1 forever (DB showed 7336).
        Now: the first call sets status='failed' (terminal); every
        subsequent call is a no-op because the WHERE filter excludes
        the terminal row. attempts stays at 1, not 20 and not 7336."""
        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=0)
        indexer = _make_indexer_for(doc)

        for _ in range(20):
            indexer._emergency_status_update("failed")

        doc.refresh_from_db()
        assert doc.indexing_attempts == 1  # not 20, not 7336
        assert doc.indexing_status == "failed"

    def test_below_max_attempts_increments_and_sets_status(self):
        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=0)
        indexer = _make_indexer_for(doc)

        indexer._emergency_status_update("failed")

        doc.refresh_from_db()
        assert doc.indexing_attempts == 1
        assert doc.indexing_status == "failed"
