"""Packaging guards for the dj-data-room extraction (Plan Phase 4A, A0-A10).

AST-scans every data_room production module (everything except ``tests/``)
and asserts that external imports stay inside an explicit whitelist.

Final state (step A10): the host-coupling whitelist that was narrowed step
by step (A1-A9) is gone — data_room may only import Django/3rd-party
libraries and the peer packages (``scribe``, ``progress``, ``ai_router``).
Host integration happens exclusively via settings hooks (``data_room.hooks``,
``data_room.conf``), the permission policy (``DATA_ROOM_PERMISSION_POLICY``)
and the ``project_zip_downloaded`` signal — data_room can leave the monorepo.
"""

import ast
import sys
from pathlib import Path

import data_room

# Django + 3rd-party libraries the package may always import.
ALLOWED_THIRD_PARTY = {
    "PIL",
    "celery",
    "django",
    "django_otp",
    "easy_thumbnails",
    "fitz",
    "pdf2image",
    "pydantic",
}

# Peer packages (own repos, fail-fast via system checks) — allowed in the
# final state per plan step A10.
ALLOWED_PEERS = {
    "ai_router",
    "progress",
    "scribe",
}

# leasing host modules that must NEVER be imported by data_room production
# code. Static core list (plan A10) — extended dynamically with every
# installed host app via _forbidden_host_top_levels(), so newly added host
# apps are covered automatically. Independent safety net: fails even if one
# of these ever sneaked into the ALLOWED_* sets above.
FORBIDDEN_HOST_TOP_LEVELS = {
    "ai_agents",
    "history",
    "home",
    "leasing",
    "my_forms",
    "pages",
    "project",
    "users",
}


def _forbidden_host_top_levels() -> set:
    """Static core list + all installed host apps (apps whose code lives
    inside the host repo, i.e. under ``settings.BASE_DIR``), excluding only
    data_room itself. The peer packages need no name-based exclusion — they
    are installed from site-packages, so they are never under BASE_DIR (and
    a host app smuggled into ALLOWED_PEERS still gets flagged)."""
    from django.apps import apps as django_apps
    from django.conf import settings

    base_dir = Path(settings.BASE_DIR).resolve()
    discovered = set()
    for app_config in django_apps.get_app_configs():
        top_level = app_config.name.split(".")[0]
        if top_level == "data_room":
            continue
        if base_dir in Path(app_config.path).resolve().parents:
            discovered.add(top_level)
    return FORBIDDEN_HOST_TOP_LEVELS | discovered


def _iter_production_imports():
    """Yield (module_path, location) for every absolute import in data_room
    production code (tests excluded, relative imports are package-internal)."""
    package_dir = Path(data_room.__file__).resolve().parent
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir)
        if "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                yield name, f"{relative}:{node.lineno}"


def _is_allowed(module: str) -> bool:
    top_level = module.split(".")[0]
    if top_level in sys.stdlib_module_names:
        return True
    if top_level == "data_room":
        return True
    return top_level in ALLOWED_THIRD_PARTY or top_level in ALLOWED_PEERS


def test_data_room_imports_stay_inside_whitelist():
    """No data_room production module may grow a new external coupling."""
    offenders = sorted(
        f"{location}: {module}" for module, location in _iter_production_imports() if not _is_allowed(module)
    )
    assert offenders == [], "data_room imports outside the packaging whitelist:\n" + "\n".join(offenders)


def test_no_host_app_imports_remain():
    """A10 final gate: zero imports of leasing host apps, regardless of the
    ALLOWED_* sets above."""
    forbidden = _forbidden_host_top_levels()
    offenders = sorted(
        f"{location}: {module}"
        for module, location in _iter_production_imports()
        if module.split(".")[0] in forbidden
    )
    assert offenders == [], "data_room still imports leasing host apps:\n" + "\n".join(offenders)


def test_allowed_sets_contain_no_host_apps():
    """The allow-lists themselves must never be widened with host apps."""
    overlap = sorted((ALLOWED_THIRD_PARTY | ALLOWED_PEERS) & _forbidden_host_top_levels())
    assert overlap == [], "host apps smuggled into the allow-lists: " + ", ".join(overlap)


def test_templates_load_no_host_template_tag_libraries():
    """A9: data_room templates must not load host template-tag libraries
    (``ai_agents_tags``, ``qsargs_tags``) — the only consumers moved to the
    project app."""
    forbidden = ("ai_agents_tags", "qsargs_tags")
    templates_dir = Path(data_room.__file__).resolve().parent / "templates"
    offenders = []
    for path in sorted(templates_dir.rglob("*.html")):
        text = path.read_text()
        for library in forbidden:
            if library in text:
                offenders.append(f"{path.relative_to(templates_dir)}: {library}")
    assert offenders == [], "data_room templates load host tag libraries:\n" + "\n".join(offenders)
