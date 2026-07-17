"""Generate the Sheriff-holdover case PDFs + clause sidecars (a second, independent
test case for the same infrastructure). Two docs so retrieval must choose:
  - article12_holdover.pdf  : the governing policy (12.1/12.2/12.3)
  - uniform_allowance.pdf   : an unrelated distractor
Sidecars carry exact bboxes; the MOU boxes match rules_ratified.json.
"""
import json
import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "cases", "sheriff", "sources")
PAGE_W, PAGE_H = letter

ART12 = [
    ("12.1", [60, 690, 552, 630],
     "Section 12.1: Correctional officers who are mandated to work a holdover past their "
     "scheduled shift on a designated High-Security Day shall receive their base hourly "
     "rate, plus a holdover premium of 1.0x their base rate for all held-over hours."),
    ("12.2", [60, 600, 552, 510],
     "Section 12.2 - Graveyard Exception: If an officer's regular assignment is the "
     "graveyard shift, they do not receive the 1.0x holdover premium. Instead, they "
     "receive a flat $200 night-holdover stipend for the shift, paid at straight time."),
    ("12.3", [60, 480, 552, 420],
     "Section 12.3 - Hazard Premium: Officers holding an active Hazmat Certification "
     "receive an 8% bump to their base hourly rate. This 8% must be factored into the "
     "base rate before the holdover multiplier is applied."),
]


def _wrap(text, width=95):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def make_policy(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(60, 740, "Sheriff MOU — Article 12: Holdover & Premiums")
    c.setFont("Helvetica", 10)
    out = []
    for clause, bbox, text in ART12:
        left, top, right, bottom = bbox
        y = top - 12
        for line in _wrap(text):
            c.drawString(left + 4, y, line); y -= 14
        out.append({"clause": clause, "text": text, "page": 1, "bbox": bbox,
                    "char_span": [0, len(text)]})
    c.showPage(); c.save()
    _sidecar(path, out,
             "Sheriff MOU Article 12 governs mandatory holdover pay for correctional "
             "officers on High-Security Days, including holdover premiums, a graveyard-"
             "shift exception, and a hazmat certification premium.")


def make_uniform(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(60, 740, "Sheriff Department Uniform Allowance Policy FY26")
    c.setFont("Helvetica", 10)
    for i, line in enumerate([
            "All sworn personnel receive an annual uniform allowance of $900,",
            "paid in two installments. This policy covers uniform items and",
            "cleaning only. It contains no overtime, holdover, or premium pay rules."]):
        c.drawString(64, 700 - i * 16, line)
    text = ("FY26 uniform allowance policy for sworn Sheriff personnel: a $900 annual "
            "allowance for uniforms and cleaning. No overtime or holdover pay rules.")
    clauses = [{"clause": "", "text": text, "page": 1, "bbox": [60, 700, 552, 650],
                "char_span": [0, len(text)]}]
    c.showPage(); c.save()
    _sidecar(path, clauses,
             "FY26 Sheriff uniform allowance policy: $900/year for uniforms and cleaning. "
             "No overtime or holdover rules.")


def _sidecar(pdf_path, clauses, summary):
    base, _ = os.path.splitext(pdf_path)
    with open(base + ".clauses.json", "w") as f:
        json.dump({"text": "\n".join(c["text"] for c in clauses),
                   "summary_hint": summary, "clauses": clauses}, f, indent=2)


if __name__ == "__main__":
    os.makedirs(SRC, exist_ok=True)
    make_policy(os.path.join(SRC, "article12_holdover.pdf"))
    make_uniform(os.path.join(SRC, "uniform_allowance.pdf"))
    print("wrote sheriff PDFs + sidecars to", os.path.normpath(SRC))
