# data_room/views/api/protected_document.py
import os
import tempfile
from typing import BinaryIO, Optional, Tuple

import fitz
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from pdf2image import convert_from_path

from data_room.conf import get_login_url
from data_room.models import ProtectedClientDocument, ProtectedProjectDocument
from data_room.policies import get_policy
from data_room.utils import create_watermarks, encode_to_base64, process_image


class DocumentProcessor:
    def __init__(self, user_email: str):
        self.watermark1, self.watermark2, self.empty = create_watermarks(user_email)

    def process_page(self, image_path: str, user_type: str) -> Tuple[str, str]:
        watermark_filename = process_image(image_path, self.watermark1, self.watermark2, self.empty, user_type)
        return image_path, watermark_filename

    def generate_pdf(self, protected_document) -> bytes:
        document_type = protected_document.get_type()
        if document_type == "pdf":
            return self._generate_pdf_from_pdf(protected_document)
        else:
            return self._generate_pdf_from_image(protected_document)

    def _generate_pdf_from_pdf(self, protected_document) -> bytes:
        with tempfile.TemporaryDirectory() as path:
            pdf_download = fitz.open()
            page_images = convert_from_path(
                protected_document.file.path,
                output_folder=path,
                size=1200,
                fmt="png",
                thread_count=10,
            )

            processed_pages = []
            for idx, page_image in enumerate(page_images):
                try:
                    filename = page_image.filename
                    user_type = protected_document.user_type
                    watermark_filename = process_image(
                        filename,
                        self.watermark1,
                        self.watermark2,
                        self.empty,
                        user_type,
                    )
                    img_doc = fitz.open()
                    img_pdf_page = img_doc.new_page()
                    img_pdf_page.insert_image(img_pdf_page.rect, filename=watermark_filename)
                    processed_pages.append(img_doc)
                    os.remove(filename)
                    os.remove(watermark_filename)
                except Exception as e:
                    for doc in processed_pages:
                        doc.close()
                    raise Exception(f"Error processing page {idx}: {str(e)}")

            for img_doc in processed_pages:
                pdf_download.insert_pdf(img_doc)
                img_doc.close()
            return pdf_download.write(
                garbage=True,
                clean=True,
                deflate=True,
                deflate_images=True,
                compression_effort=100,
            )

    def _generate_pdf_from_image(self, protected_document) -> bytes:
        with tempfile.TemporaryDirectory():
            watermark_filename = process_image(
                protected_document.file.path,
                self.watermark1,
                self.watermark2,
                self.empty,
                protected_document.user_type,
            )

            img_doc = fitz.open()
            img_pdf_page = img_doc.new_page()
            img_pdf_page.insert_image(img_pdf_page.rect, filename=watermark_filename)

            pdf_bytes = img_doc.write(
                garbage=True,
                clean=True,
                deflate=True,
                deflate_images=True,
                compression_effort=100,
            )
            img_doc.close()
            os.remove(watermark_filename)

            return pdf_bytes

    def process_single_page(
        self, protected_document, page_number: Optional[int] = 1
    ) -> Tuple[str, Optional[int], Optional[int], Optional[int]]:
        previous = None
        next_page = None
        total_pages = None
        if protected_document.get_type() == "pdf":
            page_number = int(page_number)
            pdf_document = fitz.open(protected_document.file.path)
            total_pages = pdf_document.page_count
            pdf_document.close()
            first_page = page_number
            last_page = first_page
            with tempfile.TemporaryDirectory() as path:
                page_image = convert_from_path(
                    protected_document.file.path,
                    output_folder=path,
                    size=1200,
                    fmt="png",
                    first_page=first_page,
                    last_page=last_page,
                )
                filename = page_image[0].filename
                watermark_filename = process_image(
                    filename,
                    self.watermark1,
                    self.watermark2,
                    self.empty,
                    protected_document.user_type,
                )
                image_data = encode_to_base64(watermark_filename)
                os.remove(filename)
                os.remove(watermark_filename)
            if page_number:
                page_number = int(page_number)
                if page_number > 1:
                    previous = page_number - 1
                if page_number < total_pages:
                    next_page = page_number + 1
        else:
            watermark_filename = process_image(
                protected_document.file.path,
                self.watermark1,
                self.watermark2,
                self.empty,
                protected_document.user_type,
            )
            image_data = encode_to_base64(watermark_filename)
            os.remove(watermark_filename)
        return image_data, previous, next_page, total_pages


def create_download_response(content: BinaryIO, filename: str, content_type: str) -> HttpResponse:
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Content-Transfer-Encoding"] = "binary"
    return response


def api_document_page(request, pk: int) -> HttpResponse:
    user = request.user
    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if not protected_document.check_permissions(user):
        if not user.is_authenticated:
            login_url = f"{get_login_url()}?next={request.path}"
            return redirect(login_url)
        else:
            raise PermissionDenied
    processor = DocumentProcessor(request.user.email)
    page_number = request.GET.get("page", "1")
    image_data, previous, next_page, total_pages = processor.process_single_page(protected_document, page_number)
    context = {
        "protected_document": protected_document,
        "page": image_data,
        "previous": previous,
        "next": next_page,
        "page_number": page_number,
        "total_pages": total_pages,
        "url": reverse("data_room:api-page", kwargs={"pk": protected_document.pk}),
    }
    return render(request, "data_room/_show_protected_page.html", context)


def client_document_page(request, pk: int) -> HttpResponse:
    user = request.user
    protected_document = get_object_or_404(ProtectedClientDocument, pk=pk)
    if not protected_document.check_permissions(user):
        if not user.is_authenticated:
            login_url = f"{get_login_url()}?next={request.path}"
            return redirect(login_url)
        else:
            raise PermissionDenied
    processor = DocumentProcessor(request.user.email)
    page_number = request.GET.get("page", "1")
    image_data, previous, next_page, total_pages = processor.process_single_page(protected_document, page_number)
    context = {
        "protected_document": protected_document,
        "page": image_data,
        "previous": previous,
        "next": next_page,
        "page_number": page_number,
        "total_pages": total_pages,
        "url": reverse("data_room:client-doc-page", kwargs={"pk": protected_document.pk}),
    }
    return render(request, "data_room/_show_protected_page.html", context)


def api_protected_document_pdf(request, pk: int) -> HttpResponse:
    user = request.user
    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if not protected_document.check_permissions(user):
        if not user.is_authenticated:
            login_url = f"{get_login_url()}?next={request.path}"
            return redirect(login_url)
        else:
            raise PermissionDenied
    processor = DocumentProcessor(request.user.email)
    pdf_bytes = processor.generate_pdf(protected_document)
    base_filename = os.path.basename(protected_document.file.path)
    filename = os.path.splitext(base_filename)[0] + ".pdf"
    return create_download_response(pdf_bytes, filename, "application/pdf")


def api_protected_document_original(request, pk: int) -> HttpResponse:
    user = request.user
    protected_document = get_object_or_404(ProtectedProjectDocument, pk=pk)
    if not get_policy().can_download_original(user, protected_document):
        if not user.is_authenticated:
            login_url = f"{get_login_url()}?next={request.path}"
            return redirect(login_url)
        else:
            raise PermissionDenied
    if protected_document.original:
        file_field = protected_document.original
    else:
        file_field = protected_document.file
    file_name = file_field.name
    file_path = file_field.path
    if not file_field.storage.exists(file_name):
        raise Http404(f"File not found on disk: {file_name}")
    with file_field.open("rb") as f:
        content_bytes = f.read()
    content_type_map = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "odt": "application/vnd.oasis.opendocument.text",
        "pdf": "application/pdf",
    }
    file_ext = os.path.splitext(file_name)[1].lower().replace(".", "")
    content_type = content_type_map.get(file_ext, "application/octet-stream")
    filename = os.path.basename(file_path)
    return create_download_response(content_bytes, filename, content_type)
