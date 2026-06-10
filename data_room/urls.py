from django.urls import path

from . import views

app_name = "data_room"
urlpatterns = [
    path("document/<int:pk>/", views.detail_view, name="detail"),
    path("redirect/<int:pk>/", views.redirect_view, name="redirect"),
    path("chunks/<int:pk>/", views.document_chunks_view, name="document_chunks"),
    path("page/<int:pk>/", views.api_document_page, name="api-page"),
    path("pdf/<int:pk>/", views.api_protected_document_pdf, name="api-pdf"),
    path("original/<int:pk>/", views.api_protected_document_original, name="api-original"),
    path("create/<int:pk>/", views.create_view, name="create"),
    path("reviewed/<int:pk>/", views.protected_document_reviewed, name="reviewed"),
    path("disabled/<int:pk>/", views.protected_document_disabled, name="disabled"),
    path("enable/<int:pk>/", views.protected_document_enable, name="enable"),
    path("toggle-ai/<int:pk>/", views.toggle_ai, name="toggle-ai"),
    path("delete/<int:pk>/", views.protected_document_delete, name="delete"),
    # Client document actions
    path("client-doc/page/<int:pk>/", views.client_document_page, name="client-doc-page"),
    path("client-doc/reviewed/<int:pk>/", views.client_document_reviewed, name="client-doc-reviewed"),
    path("client-doc/disabled/<int:pk>/", views.client_document_disabled, name="client-doc-disabled"),
    path("client-doc/enable/<int:pk>/", views.client_document_enable, name="client-doc-enable"),
    path("client-doc/delete/<int:pk>/", views.client_document_delete, name="client-doc-delete"),
    path("client-doc/pdf/<int:pk>/", views.client_document_pdf, name="client-doc-pdf"),
    path("client-doc/original/<int:pk>/", views.client_document_original, name="client-doc-original"),
    path("client-doc/toggle-ai/<int:pk>/", views.client_document_toggle_ai, name="client-doc-toggle-ai"),
    path("delete-all/<int:pk>/", views.protected_document_delete_all, name="delete-all"),
    path("hx/empty/<int:pk>/", views.hx_empty, name="hx-empty"),
    path("hx/<int:pk>/upload/project/", views.hx_upload_protected_project_document, name="hx-upload-project"),
    path("hx/<int:pk>/upload/client/", views.hx_upload_protected_client_document, name="hx-upload-client"),
    path(
        "project-zip/start/<int:pk>/",
        views.start_project_zip_generation,
        name="start-project-zip",
    ),
    path(
        "project-zip/download/<int:pk>/",
        views.download_project_zip,
        name="download-project-zip",
    ),
    path(
        "project-zip/progress/<int:pk>/",
        views.project_zip_progress_view,
        name="project-zip-progress",
    ),
    path(
        "hx/project-zip-button/<int:pk>/",
        views.hx_project_zip_button,
        name="hx-project-zip-button",
    ),
    path("reset-index/", views.reset_index, name="reset_index"),
    path("index-documents/", views.index_documents, name="index_documents"),
    path("count-indexed-chunks/", views.count_indexed_chunks, name="count_indexed_chunks"),
    # Client extraction confirmation (only for ProtectedClientDocument)
    path(
        "hx/confirm-clients-client-doc/<int:document_pk>/",
        views.hx_confirm_clients_client_doc,
        name="hx-confirm-clients-client-doc",
    ),
    path(
        "hx/skip-clients-client-doc/<int:document_pk>/",
        views.hx_skip_client_extraction_client_doc,
        name="hx-skip-clients-client-doc",
    ),
    path(
        "hx/trigger-extraction-client-doc/<int:document_pk>/",
        views.hx_trigger_client_extraction_client_doc,
        name="hx-trigger-extraction-client-doc",
    ),
    path(
        "hx/close-modal/",
        views.hx_close_modal,
        name="hx-close-modal",
    ),
    path(
        "hx/<int:pk>/edit-client/<int:document_pk>/",
        views.hx_edit_client,
        name="hx-edit-client",
    ),
    path(
        "hx/edit-client-client-doc/<int:document_pk>/",
        views.hx_edit_client_client_doc,
        name="hx-edit-client-client-doc",
    ),
    path(
        "hx/toggle-company-link/<int:project_pk>/<int:client_pk>/",
        views.hx_toggle_company_link,
        name="hx-toggle-company-link",
    ),
]
