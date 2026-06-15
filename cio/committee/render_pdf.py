"""
render_pdf.py — Markdown → PDF renderer for committee reports.

Lazy-imports markdown + weasyprint (heavy native libs) inside the render
function so module load stays cheap (tests, bot startup, CLI imports).
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_CSS = """
@page {
    size: A4;
    margin: 1.6cm;
    @bottom-center { content: counter(page); }
}
body {
    font-family: "Noto Sans CJK TC", "Noto Sans CJK JP", "DejaVu Sans", sans-serif;
    font-size: 10.5pt;
    line-height: 1.45;
    color: #1a1a1a;
}
h1 {
    font-size: 18pt;
    font-weight: bold;
    margin-bottom: 6pt;
}
h2 {
    font-size: 13pt;
    font-weight: bold;
    border-bottom: 1px solid #bbb;
    padding-bottom: 2pt;
    margin-top: 14pt;
    margin-bottom: 4pt;
}
h3 {
    font-size: 11pt;
    font-weight: bold;
    margin-top: 10pt;
    margin-bottom: 3pt;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 6pt 0;
}
th, td {
    border: 1px solid #bbb;
    padding: 4px 6px;
    font-size: 9.5pt;
}
th {
    background: #f0f0f3;
    font-weight: bold;
}
p {
    margin: 4pt 0;
}
code {
    font-family: monospace;
    font-size: 9pt;
    background: #f5f5f5;
    padding: 1px 3px;
}
"""

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _figures_html(appendix_images, section_title: str) -> str:
    """Build an embedded-image appendix section. Images are inlined as base64
    data URIs so rendering never depends on cwd or weasyprint base_url."""
    import base64
    import mimetypes

    figs = []
    for caption, path in appendix_images:
        try:
            data = Path(path).read_bytes()
        except Exception:
            continue
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        cap = f"<figcaption>{caption}</figcaption>" if caption else ""
        figs.append(
            f'<figure style="margin:8pt 0;text-align:center">'
            f'<img src="data:{mime};base64,{b64}" '
            f'style="max-width:100%;height:auto"/>{cap}</figure>'
        )
    if not figs:
        return ""
    return f'<h2 style="page-break-before:always">{section_title}</h2>' + "".join(figs)


def markdown_to_pdf(
    md: str,
    out_path: "str | Path",
    title: str = "Investment Committee Report",
    appendix_images: "list[tuple[str, str]] | None" = None,
    appendix_title: str = "技術指標 (指標視覺化)",
) -> str:
    """
    Render a Markdown string to a PDF file at *out_path*.

    Uses:
      markdown (tables/fenced_code/sane_lists extensions) → HTML
      weasyprint → PDF with embedded Noto Sans CJK TC (handles TC + Latin).

    ``appendix_images`` — optional ``[(caption, image_path), ...]`` appended as a
    final figure section (e.g. the indicator-visualization chart). Images are
    base64-embedded, so a missing/unreadable file is skipped silently and never
    breaks the report.

    Lazy-imports both heavy libs inside this function — never at module load.
    Raises on failure so the caller can decide the fallback strategy.

    Returns str(out_path).
    """
    # Lazy imports — heavy native libs; keep module-level import cost zero.
    import logging as _logging
    import markdown as _markdown  # pip: markdown
    import weasyprint  # pip: weasyprint

    # WeasyPrint's font subsetting (fontTools) floods the log at DEBUG/INFO on every
    # render. Cap these third-party loggers at WARNING so a /committee PDF doesn't
    # bury the app's own logs. Idempotent; affects only these named loggers.
    for _noisy in ("fontTools", "fontTools.subset", "fontTools.ttLib", "weasyprint"):
        _logging.getLogger(_noisy).setLevel(_logging.WARNING)

    html_body = _markdown.markdown(
        md,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    if appendix_images:
        html_body += _figures_html(appendix_images, appendix_title)
    full_html = _HTML_TEMPLATE.format(title=title, css=_CSS, body=html_body)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    weasyprint.HTML(string=full_html).write_pdf(str(out))
    return str(out)
