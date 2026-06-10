# data_room/tasks/index_document.py
import asyncio
import logging
import random
import time
import traceback
from typing import Any, Dict, Optional

from celery import shared_task
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.utils import timezone
from progress.services import ProgressTrackerService, map_progress
from scribe.scribe_milvus import SCRIBE
from scribe.tools.ocr_processor import extract_markdown_with_ocr
from scribe.utils import tiktoken_length

from data_room import hooks
from data_room.conf import get_project_model
from data_room.helpers.get_document import get_document
from data_room.helpers.update_document_status import update_document_status
from data_room.models import ProtectedProjectDocument

Project = get_project_model()
UserModel = get_user_model()

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def extract_markdown_task(
    self,
    document_id,
    user_id,
    model_name="ProtectedDocument",
    pipeline_progress_id=None,
    progress_range=None,
):
    """Extract markdown from a document via OCR/pymupdf4llm.

    DEPRECATED in the upload pipeline: markdown now comes from the LLM via
    extract_document_data_task. This task remains only for manual re-extraction
    (admin actions, CLI reextract_markdown command).
    """
    document = get_document(document_id, model_name)

    if document.markdown:
        logger.info(f"Document {document_id} already has markdown, skipping extraction")
        if pipeline_progress_id and progress_range:
            ProgressTrackerService.update_progress(
                pipeline_progress_id,
                map_progress(100, *progress_range),
                info_txt="Markdown bereits vorhanden",
            )
        return {"status": "skipped", "document_id": document_id}

    if pipeline_progress_id and progress_range:
        ProgressTrackerService.update_progress(
            pipeline_progress_id,
            map_progress(5, *progress_range),
            info_txt="Markdown-Extraktion gestartet",
        )

    file_path = document.file.path
    logger.info(f"Extracting markdown for document {document_id}: {file_path}")
    document.markdown = extract_markdown_with_ocr(file_path)
    document.tokens = tiktoken_length(document.markdown)
    document.save(skip_preview=True)

    if pipeline_progress_id and progress_range:
        ProgressTrackerService.update_progress(
            pipeline_progress_id,
            map_progress(100, *progress_range),
            info_txt="Markdown-Extraktion abgeschlossen",
        )

    logger.info(f"Markdown extracted for document {document_id} ({document.tokens} tokens)")
    return {"status": "success", "document_id": document_id}


class DocumentIndexer:
    """
    Class responsible for handling document indexing process with robust error handling,
    state management, and exponential backoff for retries.
    """

    # Maximum number of retries for Vector Store connection
    MAX_VECTOR_STORE_RETRIES = 3
    # Initial backoff time in seconds
    INITIAL_BACKOFF = 2
    # Maximum backoff time in seconds
    MAX_BACKOFF = 30

    def __init__(
        self,
        protected_document_id: int,
        task_id: str,
        user_id: int,
        model_name: str = "ProtectedDocument",
        pipeline_progress_id=None,
        progress_range=None,
    ):
        self.protected_document_id = protected_document_id
        self.user = UserModel.objects.get(id=user_id)
        self.task_id = task_id
        self.model_name = model_name
        self.protected_document = None
        self.scribe: Optional[SCRIBE] = None
        self.progress = None
        self.milvus_connected = False
        self.pipeline_progress_id = pipeline_progress_id
        self.progress_range = progress_range
        self.use_pipeline = bool(pipeline_progress_id and progress_range)

    def _update_progress(self, local_step: int, info_txt: str) -> None:
        """Update progress via pipeline tracker or standalone tracker."""
        if self.use_pipeline:
            ProgressTrackerService.update_progress(
                self.pipeline_progress_id,
                map_progress(local_step, *self.progress_range),
                info_txt=info_txt,
            )
        elif self.progress:
            ProgressTrackerService.update_progress(self.progress.id, local_step, info_txt=info_txt)

    def _update_indexing_progress(self, processed_batches: int, total_batches: int) -> None:
        """Update progress during batch indexing."""
        # Map batch progress to 50-90% of local range
        local_percent = int((processed_batches / total_batches) * 40 + 50)
        self._update_progress(local_percent, f"Indexing batch {processed_batches}/{total_batches}")

    def initialize(self):
        """Initialize processing by fetching document and updating its status."""
        # Fetch the document with a lock to prevent concurrent processing
        self.protected_document = get_document(self.protected_document_id, self.model_name)

        # Validate current status and update to processing
        current_status = self.protected_document.indexing_status

        # If document is already in an active processing state, abort
        # Exception: pipeline mode — the pipeline itself sets "processing" before calling us
        if current_status in ["processing", "chunking", "indexing"] and not self.use_pipeline:
            logger.warning(
                f"Document ID {self.protected_document_id} already in active state '{current_status}'. "
                f"Aborting to prevent duplicate processing."
            )
            raise RuntimeError(f"Document already in active processing state: {current_status}")

        # If the document has a status that can't transition to processing, abort
        if current_status not in ["pending", "indexed", "failed", "processing"]:
            logger.warning(
                f"Document ID {self.protected_document_id} is in state '{current_status}' "
                f"which cannot transition to 'processing'. Aborting."
            )
            raise RuntimeError(f"Invalid document state for processing: {current_status}")

        # Update status to processing - will return False if the update fails
        if not update_document_status(self.protected_document, "processing"):
            logger.error(f"Failed to update document ID {self.protected_document_id} to 'processing' state")
            raise RuntimeError("Failed to update document status to processing")

    def process_document(self):
        """Main method to process and index a document with robust error handling."""
        backoff_time = self.INITIAL_BACKOFF

        try:
            # Initialize the document processing
            self.initialize()
            logger.info(f"Starting document indexing for ID: {self.protected_document_id}")

            # Initialize progress tracking (only in standalone mode)
            if not self.use_pipeline:
                self.progress = ProgressTrackerService.create_task(
                    user=self.protected_document.user,
                    task_type="document_indexing",
                    total_steps=100,
                    task_object_id=str(self.protected_document_id),
                    metadata={
                        "document_id": self.protected_document_id,
                        "document_name": self.protected_document.name,
                    },
                )

            # Step 1: Check vector store health (backend-agnostic) and initialize
            health_check_passed = False

            for attempt in range(1, self.MAX_VECTOR_STORE_RETRIES + 1):
                logger.info(f"Performing vector store health check (attempt {attempt}/{self.MAX_VECTOR_STORE_RETRIES})")
                try:
                    from scribe.backends import get_search_backend

                    if get_search_backend().is_ready():
                        health_check_passed = True
                        break
                except Exception as e:
                    logger.warning(f"Health check attempt {attempt} failed: {str(e)}")

                if attempt < self.MAX_VECTOR_STORE_RETRIES:
                    wait_time = min(backoff_time * (2 ** (attempt - 1)), self.MAX_BACKOFF)
                    logger.info(f"Health check failed. Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)

            if not health_check_passed:
                raise RuntimeError(f"Vector store health check failed after {self.MAX_VECTOR_STORE_RETRIES} attempts")

            # Once health check passes, initialize vector store
            self._initialize_vector_store()
            self._update_progress(10, "Vector store initialized")

            # Step 2: Process and index document
            self._process_and_index()

            # Step 3: Calculate tokens
            self._calculate_tokens()
            self._update_progress(95, "Calculating document tokens")

            logger.info(f"Successfully completed indexing for document ID: {self.protected_document_id}")
            if not self.use_pipeline:
                ProgressTrackerService.complete_task(self.progress.id, success=True)

        except Exception as e:
            self._handle_error("Failed to complete document indexing", e)
            raise
        finally:
            # Always ensure vector store connection is closed
            if self.scribe:
                self.scribe.close()

    def _calculate_tokens(self) -> None:
        """Calculate token count for the document's markdown content."""
        try:
            # Skip if no markdown content
            if not self.protected_document.markdown:
                doc_id = self.protected_document_id
                logger.warning(f"Document ID {doc_id} has no markdown content to calculate tokens for")
                return

            token_count = tiktoken_length(self.protected_document.markdown)
            self.protected_document.tokens = token_count
            self.protected_document.save(skip_preview=True)

            logger.info(f"Document ID {self.protected_document_id} has {token_count} tokens")
        except Exception as e:
            self._handle_error("Failed to calculate document tokens", e)
            raise

    def _get_collection_name(self) -> str:
        """Get the Milvus collection name based on document type."""
        if hasattr(self.protected_document, "project") and self.protected_document.project:
            return f"project_{self.protected_document.project.id}"
        elif hasattr(self.protected_document, "client") and self.protected_document.client:
            return f"client_{self.protected_document.client.id}"
        else:
            raise RuntimeError("Document has neither project nor client association")

    def _initialize_vector_store(self) -> None:
        """Initialize vector store with exponential backoff for retries."""
        retry_count = 0
        backoff_time = self.INITIAL_BACKOFF

        while retry_count < self.MAX_VECTOR_STORE_RETRIES:
            try:
                collection_name = self._get_collection_name()
                self.scribe = SCRIBE(collection_name)
                collection_status = self.scribe.initialize_collection()

                if not collection_status["success"]:
                    logger.error(
                        f"Failed to initialize vector store for collection '{collection_name}' "
                        f"(attempt {retry_count + 1}/{self.MAX_VECTOR_STORE_RETRIES})"
                    )
                    raise RuntimeError("Failed to initialize vector store")

                # If successful, mark as connected and return
                self.milvus_connected = True
                logger.info(f"Successfully initialized vector store for collection '{collection_name}'")
                return

            except Exception as e:
                retry_count += 1
                if retry_count >= self.MAX_VECTOR_STORE_RETRIES:
                    logger.error(
                        f"Failed to initialize vector store after {self.MAX_VECTOR_STORE_RETRIES} attempts: {str(e)}"
                    )
                    self._handle_error("Failed to initialize vector store after max retries", e)
                    raise

                # Calculate backoff time with exponential increase and jitter
                wait_time = min(backoff_time * (2 ** (retry_count - 1)), self.MAX_BACKOFF)
                # Add some randomness (jitter) to prevent thundering herd problem
                wait_time = wait_time * (0.9 + 0.2 * random.random())

                logger.warning(
                    f"Vector store initialization failed (attempt {retry_count}/{self.MAX_VECTOR_STORE_RETRIES}). "
                    f"Retrying in {wait_time:.2f} seconds..."
                )
                time.sleep(wait_time)

                # Close any failed connection before retrying
                if self.scribe:
                    try:
                        self.scribe.close()
                    except Exception:
                        pass
                    self.scribe = None

    def _process_and_index(self) -> None:
        """Process document and index its content into the vector store."""
        try:
            # Update status to chunking
            if not update_document_status(self.protected_document, "chunking"):
                logger.error(f"Failed to update document ID {self.protected_document_id} to 'chunking' state")
                raise RuntimeError("Failed to update document status")

            # Process the PDF
            self._update_progress(15, "Starting PDF processing")
            doc_list = self.scribe.process_pdf(self.protected_document, user_id=self.user.id)
            self._update_progress(50, "PDF processed and chunked")
            if not doc_list:
                logger.warning(f"No document chunks found for document ID: {self.protected_document.id}")
                # Successfully processed but found no chunks - mark as indexed
                update_document_status(self.protected_document, "indexed")
                return

            # Get the document text for contextualization
            document_text = None
            if hasattr(self.protected_document, "markdown") and self.protected_document.markdown:
                document_text = self.protected_document.markdown
                logger.info(f"Using document markdown for contextualization (length: {len(document_text)})")

            # Update status to indexing
            if not update_document_status(self.protected_document, "indexing"):
                logger.error(f"Failed to update document ID {self.protected_document_id} to 'indexing' state")
                raise RuntimeError("Failed to update document status")

            # Add documents to collection asynchronously
            async def add_docs_async():
                try:
                    await self.scribe.add_documents_to_collection(
                        documents=doc_list,
                        batch_size=100,
                        document_text=document_text,
                        progress_callback=self._update_indexing_progress,
                        user_id=self.user.id,
                    )
                except Exception as e:
                    logger.error(f"Error adding documents to collection: {str(e)}")
                    raise

            asyncio.run(add_docs_async())

            self._update_progress(90, "Document indexed successfully")

            # Update status to indexed
            if not update_document_status(self.protected_document, "indexed"):
                logger.error(f"Failed to update document ID {self.protected_document_id} to 'indexed' state")
                raise RuntimeError("Failed to update document status")

            # Update indexed chunks count
            self.protected_document.indexed_chunks = len(doc_list)
            self.protected_document.save(skip_preview=True)

            logger.info(f"Successfully indexed {len(doc_list)} chunks for document ID {self.protected_document_id}")

        except Exception as e:
            error_message = f"Failed to index document: {str(e)}"
            logger.error(error_message, exc_info=True)

            # Update progress tracker (standalone only — pipeline orchestrator handles completion)
            if self.progress and not self.use_pipeline:
                ProgressTrackerService.complete_task(self.progress.id, success=False, error_message=error_message)

            # Try to update status to failed
            if self.protected_document:
                try:
                    update_document_status(self.protected_document, "failed")
                except Exception as status_error:
                    logger.error(f"Failed to update document status after indexing error: {str(status_error)}")

                    # Last resort: direct database update
                    self._emergency_status_update("failed")
            raise

    def _get_table_name(self) -> str:
        """Get the database table name from the protected_document instance.

        Bug fix: the previous hardcoded fallback 'data_room_protecteddocument'
        does not exist (the abstract base has no table; subclasses get
        protectedprojectdocument / protectedclientdocument). Using the
        instance's own _meta.db_table is correct for any subclass.
        """
        if self.protected_document is not None:
            return type(self.protected_document)._meta.db_table
        return (
            "data_room_protectedclientdocument"
            if self.model_name == "ProtectedClientDocument"
            else "data_room_protectedprojectdocument"
        )

    def _emergency_status_update(self, status: str) -> None:
        """Last-resort direct SQL update when the ORM path fails.

        Codex P2 / staging-bug fix: original implementation incremented
        indexing_attempts unconditionally, so every Celery retry bumped
        the counter (DB showed 7336 attempts for doc #105). Fixes:

        - WHERE filter excludes terminal statuses ('indexed', 'failed') so
          the row is left alone once it's reached a terminal state — no
          counter bump and no date_updated churn.
        - LEAST(indexing_attempts + 1, MAX_ATTEMPTS) caps the counter so
          even a non-terminal row can't blow past MAX.
        - When attempts reach MAX_ATTEMPTS the status is forced to 'failed'
          regardless of the requested status — caller intent is satisfied
          (caller passes 'failed' anyway in current code paths) and the
          contract with the state machine is preserved.
        """
        try:
            from django.db import connection

            from data_room.helpers.update_document_status import MAX_ATTEMPTS

            table_name = self._get_table_name()
            stmt = (
                f"UPDATE {table_name} SET "
                "indexing_status = CASE WHEN indexing_attempts + 1 >= %s THEN 'failed' ELSE %s END, "
                "indexing_attempts = LEAST(indexing_attempts + 1, %s), "
                "date_updated = %s "
                "WHERE id = %s AND indexing_status NOT IN ('indexed', 'failed')"
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    stmt,
                    [
                        MAX_ATTEMPTS,
                        status,
                        MAX_ATTEMPTS,
                        timezone.now(),
                        self.protected_document.id,
                    ],
                )
        except Exception as db_error:
            logger.error(f"Emergency status update also failed: {str(db_error)}")

    def _handle_error(self, message: str, exception: Exception = None) -> None:
        """Handle errors during document processing with detailed logging."""
        # Generate detailed error information with stack trace if available
        if exception:
            traceback_str = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
            error_detail = f"{message}: {str(exception)}"
            logger.error(f"{error_detail}\n\n{traceback_str}", exc_info=True)
        else:
            error_detail = message
            logger.error(error_detail)

        # Update progress tracker if available (standalone only)
        if self.progress and not self.use_pipeline:
            try:
                ProgressTrackerService.complete_task(self.progress.id, success=False, error_message=error_detail)
            except Exception as e:
                logger.error(f"Failed to update progress tracker: {str(e)}")

        # Update document status to failed if available
        if self.protected_document:
            try:
                update_document_status(self.protected_document, "failed")
            except Exception as e:
                logger.error(f"Failed to update document status after error: {str(e)}")

                # Last resort: direct database update
                self._emergency_status_update("failed")


@shared_task(bind=True)
def index_document_task(
    self,
    protected_document_id: int,
    user_id: int,
    model_name: str = "ProtectedDocument",
    pipeline_progress_id=None,
    progress_range=None,
) -> Dict[str, Any]:
    """
    Task to index a document with improved concurrency management and error handling.

    Args:
        protected_document_id: ID of the document to index
        user_id: ID of the user initiating the indexing
        model_name: Model class name ("ProtectedProjectDocument" or "ProtectedClientDocument")
        pipeline_progress_id: Optional UUID of the pipeline-level TaskProgress
        progress_range: Optional tuple (range_start, range_end) for progress mapping

    Returns:
        Dict with status information
    """
    from data_room.models import ProtectedClientDocument

    # Check how many documents are currently at the Milvus indexing stage.
    # Only count chunking/indexing (the actual Milvus-heavy steps), NOT processing
    # (which covers the entire pipeline including markdown/client/financial extraction).
    running_project_docs = ProtectedProjectDocument.objects.filter(indexing_status__in=["chunking", "indexing"]).count()
    running_client_docs = ProtectedClientDocument.objects.filter(indexing_status__in=["chunking", "indexing"]).count()
    active_count = running_project_docs + running_client_docs

    if active_count > 10:  # Raised from 2: per-instance Milvus aliases allow parallel indexing
        logger.warning(
            f"Too many documents ({active_count}) currently being processed. "
            f"Delaying indexing of document ID {protected_document_id}."
        )
        # Reschedule this task with exponential backoff
        retry_count = self.request.retries
        max_retries = 5

        if retry_count < max_retries:
            # Calculate backoff with jitter to prevent thundering herd problem
            backoff = min(60 * (2**retry_count), 1800)  # Max 30 minutes
            jitter = random.uniform(0.75, 1.25)
            backoff_with_jitter = int(backoff * jitter)

            logger.info(
                f"Retrying indexing of document ID {protected_document_id} "
                f"in {backoff_with_jitter} seconds (retry {retry_count + 1}/{max_retries})"
            )

            # Retry with exponential backoff
            raise self.retry(countdown=backoff_with_jitter, max_retries=max_retries)
        else:
            logger.error(
                f"Exceeded maximum retries ({max_retries}) for document ID {protected_document_id}. "
                f"Setting to 'failed' status."
            )
            try:
                model_class = (
                    ProtectedClientDocument if model_name == "ProtectedClientDocument" else ProtectedProjectDocument
                )
                document = model_class.objects.get(id=protected_document_id)
                update_document_status(document, "failed")
            except Exception as e:
                logger.error(f"Failed to set document to 'failed' status: {str(e)}")

            return {
                "status": "error",
                "document_id": protected_document_id,
                "message": f"Exceeded maximum retries for document {protected_document_id}",
            }

    # We can proceed with processing
    task_id = self.request.id or str(timezone.now().timestamp())
    indexer = DocumentIndexer(
        protected_document_id,
        task_id,
        user_id,
        model_name,
        pipeline_progress_id=pipeline_progress_id,
        progress_range=progress_range,
    )

    try:
        indexer.process_document()
        return {
            "status": "success",
            "document_id": protected_document_id,
            "message": f"Successfully indexed document {protected_document_id}",
        }
    except Exception as e:
        logger.error(f"Document indexing failed: {str(e)}", exc_info=True)
        return {
            "status": "error",
            "document_id": protected_document_id,
            "message": f"Failed to index document {protected_document_id}: {str(e)}",
        }


@shared_task(bind=True)
def process_document_pipeline_task(self, doc_ids, user_id, model_name="ProtectedDocument"):
    """Dispatch parallel pipeline tasks for a list of documents.

    Each document gets its own Celery task (_process_single_document_task)
    so multiple uploads are processed in parallel.
    """
    from data_room.models import ProtectedClientDocument

    is_client_doc = model_name == "ProtectedClientDocument"
    model_class = ProtectedClientDocument if is_client_doc else ProtectedProjectDocument

    # Atomic transition: only dispatch workers for docs that actually moved
    # from pending → queued. Docs already queued/processing are handled by
    # an earlier dispatch — avoid double-dispatch to prevent duplicate LLM
    # calls and Milvus writes.
    transitioned = model_class.objects.filter(id__in=doc_ids, indexing_status="pending")
    transitioned_ids = list(transitioned.values_list("id", flat=True))
    transitioned.update(indexing_status="queued")

    for doc_id in transitioned_ids:
        _process_single_document_task.delay(doc_id, user_id, model_name)

    logger.info(
        f"Dispatched {len(transitioned_ids)}/{len(doc_ids)} pipeline tasks for {model_name} "
        f"(skipped {len(doc_ids) - len(transitioned_ids)} already in-progress)"
    )


# Statuses from which a worker may claim a document to run the pipeline.
# Any other status means another worker is active or the doc is in a
# non-reprocessable state.
_CLAIMABLE_STATUSES = ("pending", "queued", "indexed", "failed", "skipped")


def _claim_document(model_class, doc_id: int) -> bool:
    """Atomically claim a document for pipeline processing.

    Conditional UPDATE: transitions status to `processing` only if it
    currently matches one of the claimable statuses. Returns True if
    this worker successfully claimed it, False if another worker has.
    """
    rows = model_class.objects.filter(id=doc_id, indexing_status__in=_CLAIMABLE_STATUSES).update(
        indexing_status="processing"
    )
    return rows > 0


@shared_task(bind=True)
def _process_single_document_task(self, doc_id, user_id, model_name="ProtectedDocument"):
    """Process a single document through the full pipeline.

    Single flow (client + project docs):
      1. extract_document_data_task  (all LLM extraction from cached PDF,
                                       includes Markdown via LLM)
      2. index_document_task         (chunking + Milvus, uses LLM markdown)
    """
    from data_room.models import ProtectedClientDocument

    user = UserModel.objects.get(id=user_id)
    is_client_doc = model_name == "ProtectedClientDocument"
    model_class = ProtectedClientDocument if is_client_doc else ProtectedProjectDocument

    pipeline_progress = None
    try:
        doc = model_class.objects.get(id=doc_id)
        doc_name = doc.name

        # File integrity check: docs with missing file cannot be processed.
        # Mark as terminal-failed to prevent infinite retry by periodic beat.
        if not doc.file or not doc.file.name:
            logger.warning(f"Doc {doc_id} has no file — marking as failed (terminal).")
            model_class.objects.filter(id=doc_id).update(indexing_status="failed")
            return
        try:
            if not doc.file.storage.exists(doc.file.name):
                logger.warning(f"Doc {doc_id} file missing from storage ({doc.file.name}) — marking as failed.")
                model_class.objects.filter(id=doc_id).update(indexing_status="failed")
                return
        except Exception as e:
            logger.warning(f"Doc {doc_id} file storage check failed ({e}) — marking as failed.")
            model_class.objects.filter(id=doc_id).update(indexing_status="failed")
            return

        # Atomic claim: only proceed if doc is in a claimable state.
        # Conditional UPDATE returns row-count; 0 means another worker claimed it.
        if not _claim_document(model_class, doc_id):
            logger.info(f"Skipping pipeline for doc {doc_id}: already claimed by another worker")
            return

        pipeline_progress = ProgressTrackerService.create_task(
            user=user,
            task_type="document_pipeline",
            total_steps=100,
            task_object_id=str(doc_id),
            metadata={"document_id": doc_id, "document_name": doc_name},
        )
        pp_id = str(pipeline_progress.id)

        extraction_range = (0, 70)
        idx_range = (70, 100)

        # Step 1: LLM extraction from cached PDF (incl. Markdown).
        # Resolved via hook — no extraction task configured means we jump
        # straight to the OCR markdown fallback (host without ai_agents).
        extraction_task = hooks.get_extraction_task()
        if extraction_task is None:
            logger.info(f"No extraction task configured — running OCR markdown fallback for doc {doc_id}")
            doc.refresh_from_db()
            if not doc.markdown:
                extract_markdown_task.apply(
                    args=[doc_id, user_id, model_name],
                    kwargs={"pipeline_progress_id": pp_id, "progress_range": (55, 70)},
                )
                doc.refresh_from_db()
                if not doc.markdown:
                    # OCR didn't produce markdown → skip indexing
                    logger.warning(f"No markdown after OCR for doc {doc_id}. Marking as skipped.")
                    model_class.objects.filter(id=doc_id).update(indexing_status="skipped")
                    ProgressTrackerService.complete_task(pipeline_progress.id, success=True)
                    return
            result = None
        else:
            result = extraction_task.apply(
                args=[doc_id],
                kwargs={
                    "user_id": user_id,
                    "document_model": model_name,
                    "pipeline_progress_id": pp_id,
                    "progress_range": extraction_range,
                },
            )
        if result is not None and isinstance(result.result, dict):
            status = result.result.get("status")
            if status == "failed":
                # Extraction failed. Only critical if markdown wasn't produced —
                # without markdown, Milvus indexing can't run. If markdown exists
                # (from a prior run or partial success during this run), proceed
                # with indexing despite auxiliary call failures.
                doc.refresh_from_db()
                if not doc.markdown:
                    raise RuntimeError(f"Document data extraction failed: {result.result.get('error', 'unknown')}")
                logger.warning(
                    f"Extraction reported failed for doc {doc_id} "
                    f"({result.result.get('error', 'unknown')}), but markdown exists — "
                    f"continuing with indexing."
                )
            elif status == "skipped":
                # Reload doc to check state — skipped may mean:
                #   (a) already complete → proceed with indexing
                #   (b) cannot extract (non-PDF, no file) → need OCR fallback
                doc.refresh_from_db()
                if not doc.markdown:
                    # No markdown yet → try OCR fallback (handles images + non-PDF)
                    logger.info(f"Extraction skipped without markdown for doc {doc_id}, running OCR fallback")
                    extract_markdown_task.apply(
                        args=[doc_id, user_id, model_name],
                        kwargs={"pipeline_progress_id": pp_id, "progress_range": (55, 70)},
                    )
                    doc.refresh_from_db()
                    if not doc.markdown:
                        # OCR also didn't produce markdown → skip indexing
                        logger.warning(f"No markdown after OCR for doc {doc_id}. Marking as skipped.")
                        model_class.objects.filter(id=doc_id).update(indexing_status="skipped")
                        ProgressTrackerService.complete_task(pipeline_progress.id, success=True)
                        return
                # Markdown exists (either from prior extraction or OCR) → indexing runs below

        # Step 2: Milvus indexing (uses markdown from LLM extraction or OCR fallback)
        result = index_document_task.apply(
            args=[doc_id, user_id, model_name],
            kwargs={"pipeline_progress_id": pp_id, "progress_range": idx_range},
        )
        if isinstance(result.result, dict) and result.result.get("status") == "error":
            raise RuntimeError(f"Indexing failed: {result.result.get('message', 'unknown')}")

        ProgressTrackerService.complete_task(pipeline_progress.id, success=True)

    except Exception as e:
        logger.error(f"Pipeline failed for document {doc_id}: {e}", exc_info=True)
        if pipeline_progress:
            ProgressTrackerService.complete_task(
                pipeline_progress.id,
                success=False,
                error_message=str(e),
            )
        # Reset indexing_status so the periodic task can retry later —
        # but only if the inner task hasn't terminal-marked the doc.
        # Without these guards the catch overwrites 'failed' (set by
        # update_document_status after MAX_ATTEMPTS) back to 'pending',
        # the scheduled enqueuer picks it up again, and the loop never
        # ends (staging Doc #105 reached indexing_attempts=7336 this way).
        # Skip reset when:
        #   - status is 'indexed' (success — never revert)
        #   - status is 'failed' AND attempts >= MAX_ATTEMPTS (terminal)
        from django.db.models import Q

        from data_room.helpers.update_document_status import MAX_ATTEMPTS as _MAX_ATTEMPTS

        model_class.objects.filter(id=doc_id).exclude(
            Q(indexing_status="indexed") | (Q(indexing_status="failed") & Q(indexing_attempts__gte=_MAX_ATTEMPTS))
        ).update(indexing_status="pending")


@shared_task(bind=True)
def process_pending_documents_task(self) -> Dict[str, Any]:
    """Scheduled task: enqueue indexing for documents waiting on the queue."""
    from data_room.models import ProtectedClientDocument

    try:
        # Recover stale "queued" docs (stuck >10 min) back to "pending" for re-dispatch
        from django.utils import timezone as tz

        stale_cutoff = tz.now() - tz.timedelta(minutes=10)
        stale_count = 0
        stale_count += ProtectedProjectDocument.objects.filter(
            indexing_status="queued", date_updated__lt=stale_cutoff
        ).update(indexing_status="pending")
        stale_count += ProtectedClientDocument.objects.filter(
            indexing_status="queued", date_updated__lt=stale_cutoff
        ).update(indexing_status="pending")
        if stale_count:
            logger.warning(f"Recovered {stale_count} stale queued documents back to pending")

        # Get pending project documents
        pending_project_ids = list(
            ProtectedProjectDocument.objects.filter(indexing_status="pending")
            .order_by("date_updated")
            .values_list("id", flat=True)
        )

        # Get pending client documents
        pending_client_ids = list(
            ProtectedClientDocument.objects.filter(indexing_status="pending")
            .order_by("date_updated")
            .values_list("id", flat=True)
        )

        total_pending = len(pending_project_ids) + len(pending_client_ids)

        if total_pending == 0:
            logger.info("No pending documents found for scheduled indexing run")
            return {
                "status": "success",
                "message": "No pending documents found",
                "queued_count": 0,
            }

        user_id = (
            UserModel.objects.filter(is_active=True)
            .order_by("-is_superuser", "-is_staff", "id")
            .values_list("id", flat=True)
            .first()
        )

        if not user_id:
            logger.warning("No active staff users available for document indexing context; falling back to user id 1")
            user_id = 1

        queued_count = 0

        # Queue project documents via full pipeline (extraction + indexing).
        # Bypassing extraction would re-index with stale/missing markdown.
        if pending_project_ids:
            logger.info(
                f"Found {len(pending_project_ids)} pending project documents: "
                f"{pending_project_ids[:10]}{'...' if len(pending_project_ids) > 10 else ''}"
            )
            process_document_pipeline_task.delay(pending_project_ids, user_id, "ProtectedProjectDocument")
            queued_count += len(pending_project_ids)

        # Queue client documents via full pipeline
        if pending_client_ids:
            logger.info(
                f"Found {len(pending_client_ids)} pending client documents: "
                f"{pending_client_ids[:10]}{'...' if len(pending_client_ids) > 10 else ''}"
            )
            process_document_pipeline_task.delay(pending_client_ids, user_id, "ProtectedClientDocument")
            queued_count += len(pending_client_ids)

        return {
            "status": "success",
            "queued_count": queued_count,
            "project_docs": len(pending_project_ids),
            "client_docs": len(pending_client_ids),
            "user_id": user_id,
        }

    except Exception as e:
        error_msg = f"Failed to queue pending documents for indexing: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}


@shared_task(bind=True, max_retries=5)
def index_queried_documents_task(self, object_ids, user_id: int = 1, model_name: str = "ProtectedDocument") -> None:
    """
    Task to index multiple documents in a queue.

    Args:
        object_ids: List of document IDs to index
        user_id: ID of the user initiating the indexing
        model_name: Model class name ("ProtectedProjectDocument" or "ProtectedClientDocument")
    """
    from data_room.models import ProtectedClientDocument

    model_class = ProtectedClientDocument if model_name == "ProtectedClientDocument" else ProtectedProjectDocument

    lock = None
    lock_factory = hooks.get_redis_lock_factory()
    if lock_factory is not None:
        try:
            redis_client = lock_factory()
            lock = redis_client.get_lock(
                key="indexing:all_documents",
                timeout=3600,
                auto_renew=True,
                renewal_interval=30,
            )
            if not lock.acquire():
                countdown = 60 * (2**self.request.retries)
                logger.warning(
                    f"Could not acquire lock - another indexing process is already running. "
                    f"Retrying in {countdown}s (attempt {self.request.retries + 1}/{self.max_retries})"
                )
                raise self.retry(countdown=countdown)
        except Retry:
            raise
        except Exception as e:
            logger.warning(f"Redis lock not available: {str(e)} - proceeding without lock")

    try:
        # Mark all documents as pending first to ensure they get processed
        queryset = model_class.objects.filter(id__in=object_ids)
        queryset.update(indexing_status="pending")

        # Get pending documents and process them one by one
        pending_documents = queryset.filter(indexing_status="pending")
        logger.info(f"Starting indexing for {len(pending_documents)} {model_name} documents")

        processed_count = 0
        error_count = 0

        for document in pending_documents:
            try:
                if document.file.name and document.file.storage.exists(document.file.name):
                    logger.info(f"Queueing document for indexing: {document.name} (ID: {document.id})")
                    # Schedule indexing task with some delay to prevent overloading
                    index_document_task.apply_async(
                        args=[document.id, user_id, model_name],
                        countdown=processed_count * 3,  # Stagger tasks by 3 seconds each
                    )
                    processed_count += 1
                else:
                    doc_id = document.id
                    doc_name = document.name
                    doc_path = document.file.name
                    msg = f"Skipping document indexing - file not found: {doc_name} (ID: {doc_id}, Path: {doc_path})"
                    logger.warning(msg)
                    update_document_status(document, "failed")
                    error_count += 1

            except Exception as e:
                logger.error(f"Error queueing document for indexing: {document.id}, Error: {str(e)}")
                error_count += 1
                try:
                    update_document_status(document, "failed")
                except Exception:
                    pass

        logger.info(
            f"Document indexing queue processing complete. " f"Queued: {processed_count}, Errors: {error_count}"
        )

    finally:
        if lock:
            lock.release()
            logger.info("Released indexing lock for all documents")


@shared_task(bind=True)
def detect_stuck_documents_task(self) -> None:
    """
    Periodic task to detect and reset documents stuck in processing states.
    This task should be scheduled to run periodically (e.g., every 30 minutes).
    """
    from data_room.helpers.update_document_status import MAX_STATE_TIME, check_stuck_documents

    logger.info("Running stuck document detection...")
    stuck_docs = check_stuck_documents()

    if stuck_docs:
        logger.warning(f"Found {len(stuck_docs)} stuck documents: {stuck_docs}")
        # Log details about each stuck document
        for doc_id in stuck_docs[:10]:  # Limit to first 10 to avoid excessive logging
            try:
                doc = ProtectedProjectDocument.objects.get(id=doc_id)
                logger.warning(
                    f"Stuck document: ID={doc.id}, Name={doc.name}, "
                    f"Status={doc.indexing_status}, Last updated={doc.date_updated}, "
                    f"Attempts={doc.indexing_attempts}"
                )
            except Exception as e:
                logger.error(f"Error getting details for stuck document ID {doc_id}: {str(e)}")
    else:
        logger.info("No stuck documents found.")

    # Return information about detection
    return {
        "detected_count": len(stuck_docs),
        "document_ids": stuck_docs,
        "max_state_times": MAX_STATE_TIME,
    }


@shared_task(bind=True)
def count_indexed_chunks_task(self, user_id: int) -> None:
    """
    Task to count indexed chunks for all documents and projects.

    Counts come from the DocumentChunk ORM (Postgres source of truth).
    Regression fix: the previous implementation called
    scribe.count_document_chunks() / count_project_chunks(), which never
    existed — counts were silently never updated.

    Args:
        user_id: ID of the user initiating the count
    """
    from scribe.models import DocumentChunk

    logger.info("Starting indexed chunks count for all projects")
    # A7: discover projects via distinct project_ids from the chunk table
    # instead of Project.objects.all() — only projects with indexed chunks
    # are visited (chunkless projects are left untouched).
    project_ids = (
        DocumentChunk.objects.filter(collection_name__startswith="project_")
        .exclude(project_id=None)
        .values_list("project_id", flat=True)
        .distinct()
    )
    projects = Project.objects.filter(id__in=project_ids)
    updated_projects = 0
    updated_documents = 0

    for project in projects:
        try:
            collection_name = f"project_{project.id}"

            # Count chunks for all documents in this project
            for protected_document in project.protected_documents.all():
                try:
                    chunk_count = DocumentChunk.objects.filter(
                        collection_name=collection_name, document_id=protected_document.id
                    ).count()
                    if chunk_count != protected_document.indexed_chunks:
                        protected_document.indexed_chunks = chunk_count
                        protected_document.save(skip_preview=True)
                        updated_documents += 1
                        logger.info(f"Updated document ID {protected_document.id} chunk count: {chunk_count}")
                except Exception as doc_error:
                    logger.error(f"Error counting chunks for document ID {protected_document.id}: {str(doc_error)}")

            # Count total chunks for the project
            project_chunks = DocumentChunk.objects.filter(collection_name=collection_name).count()
            if project_chunks != project.indexed_chunks:
                project.indexed_chunks = project_chunks
                project.save()
                updated_projects += 1
                logger.info(f"Updated project ID {project.id} chunk count: {project_chunks}")

        except Exception as project_error:
            logger.error(f"Error processing project ID {project.id}: {str(project_error)}")

    logger.info(f"Chunk counting complete. Updated {updated_documents} documents and {updated_projects} projects.")

    return {
        "projects_processed": len(projects),
        "projects_updated": updated_projects,
        "documents_updated": updated_documents,
    }
