"""Package signals for host integration (Plan Phase 4A, step A9).

``project_zip_downloaded``
    Sent when a user downloads a generated project ZIP archive.
    Kwargs: ``user`` (the downloading user), ``project`` (the host project).
    The leasing host connects a receiver (``history.receivers``) that writes
    a History entry; hosts without a history feature simply don't connect.
"""

import django.dispatch

project_zip_downloaded = django.dispatch.Signal()
