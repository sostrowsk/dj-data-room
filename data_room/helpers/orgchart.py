"""Org chart generation via Graphviz (dot)."""

import logging
import subprocess
from typing import Optional

from data_room.conf import get_client_company_model

ClientCompany = get_client_company_model()

logger = logging.getLogger(__name__)


def build_orgchart_dot(client_company: ClientCompany, active_ids=None) -> Optional[str]:
    """Build a Graphviz DOT string for the corporate group.

    Returns None if the company has no group (single company, no holding).
    When active_ids is provided, only companies with PKs in that set are included.
    """
    group = client_company.get_group()
    if active_ids is not None:
        group = [c for c in group if c.pk in active_ids]
    if len(group) <= 1:
        return None

    lines = [
        "digraph {",
        "    rankdir=TB;",
        "    dpi=300;",
        '    node [shape=box, style=filled, fillcolor="#f0f0f0", '
        'fontname="Helvetica", fontsize=11, margin="0.3,0.15"];',
        '    edge [color="#666666"];',
    ]

    for company in group:
        node_id = f"c{company.pk}"
        label = str(company)
        if company.legal_form and not label.endswith(company.legal_form):
            label += f"\\n{company.legal_form}"
        label = label.replace('"', '\\"')

        if company.pk == client_company.pk:
            lines.append(f'    {node_id} [label="{label}", ' f'fillcolor="#003366", fontcolor="#ffffff"];')
        else:
            lines.append(f'    {node_id} [label="{label}"];')

    for company in group:
        if company.holding_id:
            lines.append(f"    c{company.holding_id} -> c{company.pk};")

    lines.append("}")
    return "\n".join(lines)


def render_orgchart_png(dot_str: str) -> Optional[bytes]:
    """Render a DOT graph to high-resolution PNG via graphviz.

    Uses direct PNG output at 300 DPI (set in DOT graph attributes).
    Returns None on any failure (org chart is optional).
    """
    try:
        result = subprocess.run(
            ["dot", "-Tpng"],
            input=dot_str.encode(),
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
        logger.warning("dot exited with code %d: %s", result.returncode, result.stderr.decode())
        return None
    except Exception:
        logger.warning("Failed to render org chart via graphviz", exc_info=True)
        return None


def render_orgchart_svg(dot_str: str) -> Optional[str]:
    """Render a DOT graph to SVG via graphviz.

    Strips the dpi=300 setting (meant for PNG), removes XML prolog,
    makes the SVG responsive, and removes the white background.
    Returns SVG markup as string, or None on failure.
    """
    import re

    # Remove dpi=300 which makes SVG unnecessarily large
    svg_dot = dot_str.replace("    dpi=300;\n", "")

    try:
        result = subprocess.run(
            ["dot", "-Tsvg"],
            input=svg_dot.encode(),
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            svg = result.stdout.decode()
            # Strip XML prolog and DOCTYPE for inline embedding
            svg = re.sub(r"<\?xml[^?]*\?>\s*", "", svg)
            svg = re.sub(r"<!DOCTYPE[^>]*>\s*", "", svg)
            svg = re.sub(r"<!--.*?-->\s*", "", svg, flags=re.DOTALL)
            # Keep the intrinsic width/height so the browser renders at scale
            # 1.0; max-width:100% only lets it shrink on narrow screens, never
            # upscale. (dpi=300 was stripped above so pt == viewBox units.)
            svg = svg.replace("<svg ", '<svg style="max-width:100%;height:auto" ', 1)
            # Remove white background polygon
            svg = re.sub(r'<polygon fill="white"[^/]*/>', "", svg)
            return svg.strip()
        logger.warning("dot exited with code %d: %s", result.returncode, result.stderr.decode())
        return None
    except Exception:
        logger.warning("Failed to render org chart SVG via graphviz", exc_info=True)
        return None
