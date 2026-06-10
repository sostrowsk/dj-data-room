# data_room/views/project_zip.py
import logging

from django.contrib import messages
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django_otp.decorators import otp_required

from data_room.conf import get_login_url, get_project_detail_url
from data_room.models.project_zip import ProjectZip
from data_room.signals import project_zip_downloaded

logger = logging.getLogger(__name__)


def _handle_project_zip_download(request, project_zip, download_url_name, download_url_kwargs, redirect_url):
    """Common logic for handling project ZIP downloads."""
    if request.GET.get("complete"):
        # Delete the ZIP file from storage and database
        if project_zip.zip_file:
            project_zip.zip_file.delete()
        project_zip.delete()
        return redirect(redirect_url)

    if request.GET.get("download"):
        # Notify the host (e.g. history tracking) about the download
        project = project_zip.project
        user = request.user  # Use the authenticated user who is downloading
        project_zip_downloaded.send(sender=ProjectZip, user=user, project=project)

        zip_stream = project_zip.zip_file.open("rb")
        return FileResponse(
            zip_stream,
            content_type="application/zip",
            as_attachment=True,
            filename=project_zip.filename(),
        )

    # Generate the JavaScript download page
    download_url = reverse(download_url_name, kwargs=download_url_kwargs)
    response = HttpResponse(
        f"""
<!DOCTYPE html>
<html>
<head>
  <title>Downloading...</title>
  <script>
    window.onload = function() {{
      // Use iframe to trigger download without navigation
      var iframe = document.createElement('iframe');
      iframe.style.display = 'none';
      iframe.src = "{download_url}?download=true";
      document.body.appendChild(iframe);

      // Give more time for large files to start downloading
      setTimeout(function() {{
        // Clean up and redirect
        window.location.href = "{download_url}?complete=true";
      }}, 5000);
    }};
  </script>
</head>
<body>
  <p>Your download is starting. You will be redirected automatically...</p>
</body>
</html>
        """
    )
    return response


@otp_required
def download_project_zip(request, pk):
    user = request.user
    if not user.is_authenticated:
        login_url = f"{get_login_url()}?next={request.path}"
        return redirect(login_url)

    project_zip = get_object_or_404(ProjectZip, pk=pk, user=user)
    project_id = project_zip.project.pk

    if project_zip.status != "completed":
        messages.warning(request, "The ZIP file is still being generated. Please wait.")
        return redirect(reverse(get_project_detail_url(), kwargs={"pk": project_id}) + "#data-room-section")

    redirect_url = reverse(get_project_detail_url(), kwargs={"pk": project_id}) + "#data-room-section"
    return _handle_project_zip_download(
        request,
        project_zip,
        download_url_name="data_room:download-project-zip",
        download_url_kwargs={"pk": pk},
        redirect_url=redirect_url,
    )


# Update data_room/views/api/protected_document.py - modify api_protected_document_zip
def api_protected_document_zip(request, pk: int) -> HttpResponse:
    return redirect(reverse("data_room:start-project-zip", kwargs={"pk": pk}))
