from django import template

register = template.Library()


@register.simple_tag()
def get_preview(item):
    return item.get_preview()


@register.simple_tag(takes_context=True)
def check_permissions(context, item):
    user = context.request.user
    # Cache ProtectedClientDocument checks per-request to avoid N+1 on data-room lists
    # where many docs share the same client_id/state.
    from data_room.models import ProtectedClientDocument

    if not isinstance(item, ProtectedClientDocument):
        return item.check_permissions(user)

    cache = getattr(user, "_client_doc_read_cache", None)
    if cache is None:
        cache = {}
        try:
            user._client_doc_read_cache = cache
        except AttributeError:
            cache = None
    key = (item.client_id, item.user_type, item.reviewed, item.disabled, item.user_id)
    if cache is not None and key in cache:
        return cache[key]
    result = item.check_permissions(user)
    if cache is not None:
        cache[key] = result
    return result
