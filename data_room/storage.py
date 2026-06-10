"""Protected file storage for data_room (plan step W14-A8).

Copied from ``pages.storage`` so the package has no host coupling; pages
keeps its own copy for the other apps. Files are stored outside MEDIA_ROOT
under ``PROTECTED_MEDIA_ROOT`` and served via ``PROTECTED_MEDIA_URL``
(nginx internal redirect in production).
"""

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models


class ProtectedFileSystemStorage(FileSystemStorage):
    def __init__(self, *args, **kwargs):
        kwargs["location"] = settings.PROTECTED_MEDIA_ROOT
        kwargs["base_url"] = settings.PROTECTED_MEDIA_URL
        super().__init__(*args, **kwargs)


class ProtectedFileField(models.FileField):
    def __init__(self, **kwargs):
        kwargs["storage"] = ProtectedFileSystemStorage()
        super().__init__(**kwargs)
