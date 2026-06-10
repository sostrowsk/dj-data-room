# data_room/views/document_chunks.py
from ai_router.types import Document
from ai_router.utils.safe_markdown import safe_markdown_to_html
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from scribe.models import DocumentChunk

from data_room.models import ProtectedProjectDocument
from data_room.policies import get_policy


def document_chunks_view(request, pk):
    document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if not (
        get_policy().can_access_project(request.user, document.project)
        or request.session.get("project_id", None) == document.project.id
    ):
        raise PermissionDenied
    collection_name = f"project_{document.project.id}"
    chunk_rows = DocumentChunk.objects.filter(collection_name=collection_name, document_id=pk).order_by("chunk_id")
    chunks = [
        Document(
            page_content=safe_markdown_to_html(row.content, extensions=["fenced_code", "tables", "nl2br"]),
            metadata={"document_id": row.document_id, "chunk_id": row.chunk_id},
        )
        for row in chunk_rows
    ]
    return render(
        request,
        "data_room/document_chunks.html",
        {
            "document": document,
            "chunks": chunks,
        },
    )
