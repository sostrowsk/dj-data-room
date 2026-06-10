from .api.project_zip import project_zip_progress_view, start_project_zip_generation  # noqa
from .api.protected_document import api_protected_document_original  # noqa
from .api.protected_document import api_document_page, api_protected_document_pdf, client_document_page  # noqa
from .document_chunks import document_chunks_view  # noqa
from .hx.confirm_clients import (  # noqa
    hx_close_modal,
    hx_confirm_clients_client_doc,
    hx_skip_client_extraction_client_doc,
    hx_trigger_client_extraction_client_doc,
)
from .hx.edit_client import hx_edit_client, hx_edit_client_client_doc  # noqa
from .hx.empty import hx_empty  # noqa
from .hx.project_zip_button import hx_project_zip_button  # noqa
from .hx.toggle_company_link import hx_toggle_company_link  # noqa
from .hx.upload_protected_document import (  # noqa
    hx_upload_protected_client_document,
    hx_upload_protected_project_document,
)
from .management import count_indexed_chunks, index_documents, reset_index  # noqa
from .project_zip import download_project_zip  # noqa
from .protected_document import protected_document_delete  # noqa
from .protected_document import protected_document_delete_all  # noqa
from .protected_document import protected_document_disabled  # noqa
from .protected_document import protected_document_enable  # noqa
from .protected_document import protected_document_view  # noqa
from .protected_document import (  # noqa
    client_document_delete,
    client_document_disabled,
    client_document_enable,
    client_document_original,
    client_document_pdf,
    client_document_reviewed,
    client_document_toggle_ai,
    create_view,
    detail_view,
    protected_document_reviewed,
    redirect_view,
    toggle_ai,
)
