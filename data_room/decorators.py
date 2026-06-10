"""Package-local, policy-based view decorators (Plan Phase 4A, step A6).

Replaces ``leasing.decorators`` for all data_room-internal views. Permission
checks are routed through the pluggable permission policy (``get_policy()``)
instead of calling ``check_permissions`` on the host models directly:

- ``project_permission_required`` -> ``policy.can_access_project``
- ``protected_document_permission_required`` -> ``policy.can_view_project_document``

Semantics match the host decorators for the kwargs used inside data_room:
pk resolution (kwargs or first positional arg), binding the resolved object
to ``request.project`` / ``request.protected_document``, login redirect for
anonymous users, ``PermissionDenied`` for authenticated users without access
and an optional ``htmx_required`` gate (405 for non-HTMX requests).
"""

import logging
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect

from .conf import get_login_url, get_project_model
from .models import ProtectedProjectDocument
from .policies import get_policy

Project = get_project_model()

logger = logging.getLogger(__name__)


def _policy_permission_required(model_class, model_name, policy_check, pk_param="pk", htmx_required=False):
    """Generic policy-based permission decorator.

    Args:
        model_class: Model to resolve from the ``pk_param`` URL kwarg
        model_name: Request attribute name the resolved object is bound to
        policy_check: ``callable(policy, user, obj) -> bool``
        pk_param: URL parameter name for the object's primary key
        htmx_required: Whether the request must be an HTMX request
    """

    def decorator(func):
        @wraps(func)
        def _wrapped_view(request, *args, **kwargs):
            pk = kwargs.get(pk_param)
            if not pk:
                # Try to get pk from positional args if not in kwargs
                if args and pk_param == "pk":
                    pk = args[0]
                    args = args[1:]
                    kwargs[pk_param] = pk
                else:
                    raise ValueError(f"{model_name.capitalize()} permission decorator requires '{pk_param}' parameter")

            obj = get_object_or_404(model_class, pk=pk)
            setattr(request, model_name, obj)

            user = request.user
            policy = get_policy()

            if user.is_authenticated:
                has_permission = policy_check(policy, user, obj)
            else:
                # Anonymous users may lack attributes the policy relies on —
                # treat that as "no permission" (parity with leasing.decorators).
                try:
                    has_permission = policy_check(policy, user, obj)
                except AttributeError:
                    has_permission = False

            if not has_permission:
                if not user.is_authenticated:
                    login_url = f"{get_login_url()}?next={request.path}"
                    logger.info(f"Redirecting unauthenticated user to login for {model_name} {pk}")
                    return redirect(login_url)
                logger.warning(f"Permission denied for user {user} to {model_name} {pk}")
                raise PermissionDenied

            if htmx_required and not request.htmx:
                logger.warning(f"Non-HTMX request denied for {model_name} {pk} by {user}")
                return HttpResponseNotAllowed(["GET", "POST"])

            return func(request, *args, **kwargs)

        return _wrapped_view

    return decorator


def project_permission_required(view_func=None, *, htmx_required=False, pk_param="pk"):
    """Check project access via ``policy.can_access_project``.

    Usage::

        @project_permission_required
        def my_view(request, pk):
            project = request.project

        @project_permission_required(htmx_required=True)
        def my_htmx_view(request, pk):
            project = request.project
    """

    def decorator(func):
        return _policy_permission_required(
            model_class=Project,
            model_name="project",
            policy_check=lambda policy, user, project: policy.can_access_project(user, project),
            pk_param=pk_param,
            htmx_required=htmx_required,
        )(func)

    if view_func:
        return decorator(view_func)
    return decorator


def protected_document_permission_required(view_func=None, *, htmx_required=False, pk_param="pk"):
    """Check project-document read access via ``policy.can_view_project_document``.

    Usage::

        @protected_document_permission_required
        def my_view(request, pk):
            document = request.protected_document
    """

    def decorator(func):
        return _policy_permission_required(
            model_class=ProtectedProjectDocument,
            model_name="protected_document",
            policy_check=lambda policy, user, document: policy.can_view_project_document(user, document),
            pk_param=pk_param,
            htmx_required=htmx_required,
        )(func)

    if view_func:
        return decorator(view_func)
    return decorator
