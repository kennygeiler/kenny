"""Render the PRD and the architecture doc into styled, presentation-ready PDFs.

markdown -> HTML (+ print CSS) -> headless Chrome -> PDF. Chrome is used because both
documents lean on real tables, fenced ASCII flow diagrams (which need honest monospace),
and emoji status markers — all of which a hand-built reportlab layout renders badly.

Two documents, one renderer: they are a pair (the PRD owns intent, ARCHITECTURE owns
behaviour) and must never drift into different styling or be published one without the
other.

Usage:  python scripts/make_prd_pdf.py          # both
        python scripts/make_prd_pdf.py prd      # just the PRD
        python scripts/make_prd_pdf.py arch     # just the architecture doc
"""
import os
import subprocess
import sys

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

DOCS = {
    "prd": {"src": os.path.join(ROOT, "PRD.md"),
            "title": "Kenny — PRD",
            "out": os.path.expanduser("~/Kenny_PRD.pdf")},
    "arch": {"src": os.path.join(ROOT, "ARCHITECTURE.md"),
             "title": "Kenny — Technical Architecture & Execution",
             "out": os.path.expanduser("~/Kenny_Architecture.pdf")},
}

CSS = """
@page { size: Letter; margin: 16mm 14mm 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-size: 9.6pt; line-height: 1.5; color: #1a1a1a; margin: 0;
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
h1 {
  font-size: 21pt; color: #1f3a5f; margin: 0 0 2pt; letter-spacing: -0.4pt;
  border-bottom: 2.5pt solid #1f3a5f; padding-bottom: 6pt;
}
h1 + h3 { color: #4a5568; font-weight: 500; font-size: 11pt; margin: 6pt 0 14pt; }
h2 {
  font-size: 13.5pt; color: #fff; background: #1f3a5f;
  padding: 5pt 9pt; margin: 20pt 0 9pt; border-radius: 3pt;
  page-break-after: avoid; page-break-inside: avoid;
}
h3 {
  font-size: 11pt; color: #1f3a5f; margin: 14pt 0 5pt;
  border-left: 3pt solid #2c5282; padding-left: 7pt;
  page-break-after: avoid;
}
p { margin: 5pt 0; }
strong { color: #14243d; }
a { color: #2c5282; }
ul, ol { margin: 5pt 0; padding-left: 16pt; }
li { margin: 2.5pt 0; }
hr { border: 0; border-top: 1pt solid #cbd5e0; margin: 16pt 0; }

/* blockquote = the "how to read this" callout */
blockquote {
  margin: 10pt 0; padding: 8pt 11pt; background: #eef2f7;
  border-left: 3.5pt solid #2c5282; border-radius: 0 3pt 3pt 0;
  color: #2d3748; font-size: 9pt;
}
blockquote p { margin: 3pt 0; }

/* tables */
table {
  border-collapse: collapse; width: 100%; margin: 9pt 0; font-size: 8.6pt;
  page-break-inside: avoid;
}
th {
  background: #1f3a5f; color: #fff; text-align: left; font-weight: 600;
  padding: 5pt 7pt; border: 0.5pt solid #1f3a5f;
}
td { padding: 4.5pt 7pt; border: 0.5pt solid #cbd5e0; vertical-align: top; }
tbody tr:nth-child(even) { background: #f4f7fb; }

/* code + the ASCII flow diagrams */
code {
  font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 8.4pt;
  background: #eef2f7; color: #1f3a5f; padding: 1pt 3.5pt; border-radius: 2.5pt;
}
pre {
  background: #f7f9fc; border: 0.6pt solid #cbd5e0; border-left: 3pt solid #2c5282;
  border-radius: 3pt; padding: 8pt 10pt; margin: 9pt 0; overflow: visible;
  page-break-inside: avoid;
}
pre code {
  background: none; padding: 0; color: #1a2b45; font-size: 7.5pt; line-height: 1.35;
  white-space: pre;
}
th code, td code { font-size: 7.8pt; }
"""


def build_one(key: str) -> None:
    doc = DOCS[key]
    if not os.path.exists(doc["src"]):
        sys.exit(f"missing source: {doc['src']}")
    md = open(doc["src"]).read()
    body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    html_path = f"/tmp/kenny_{key}.html"
    with open(html_path, "w") as f:
        f.write(f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<title>{doc['title']}</title><style>{CSS}</style></head>"
                f"<body>{body}</body></html>")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={doc['out']}", f"file://{html_path}"],
                   check=True, capture_output=True)
    print(f"wrote {doc['out']} ({os.path.getsize(doc['out'])/1024:.0f} KB)")


def build(which: str | None = None) -> None:
    if not os.path.exists(CHROME):
        sys.exit(f"Chrome not found at {CHROME}")
    for key in ([which] if which else list(DOCS)):
        build_one(key)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg and arg not in DOCS:
        sys.exit(f"unknown document {arg!r}; expected one of {', '.join(DOCS)}")
    build(arg)
