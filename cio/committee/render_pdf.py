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

def markdown_to_pdf(
    md: str,
    out_path: "str | Path",
    title: str = "Investment Committee Report",
) -> str:
    """
    Render a Markdown string to a PDF file at *out_path*.

    Uses:
      markdown (tables/fenced_code/sane_lists extensions) → HTML
      weasyprint → PDF with embedded Noto Sans CJK TC (handles TC + Latin).

    Lazy-imports both heavy libs inside this function — never at module load.
    Raises on failure so the caller can decide the fallback strategy.

    Returns str(out_path).
    """
    # Lazy imports — heavy native libs; keep module-level import cost zero.
    import markdown as _markdown  # pip: markdown
    import weasyprint  # pip: weasyprint

    html_body = _markdown.markdown(
        md,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    full_html = _HTML_TEMPLATE.format(title=title, css=_CSS, body=html_body)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    weasyprint.HTML(string=full_html).write_pdf(str(out))
    return str(out)
