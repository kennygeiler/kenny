"""Generate the reference-case PDFs and their clause sidecars.

Produces two documents so retrieval must *choose*:
  - mou_article9.pdf     : the governing MOU (clauses 9.1/9.2/9.3)
  - salary_schedule.pdf  : an unrelated pay table

For each PDF we also write `<name>.clauses.json` — the exact clause text + page +
bounding box (PDF points, bottom-left origin, [left, top, right, bottom]). Ingestion
uses docling when available and falls back to these sidecars, so bbox highlighting is
correct either way. The MOU boxes match the seed rules in rules_ratified.json.
"""
import json
import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "cases", "overtime", "sources")
PAGE_W, PAGE_H = letter  # 612 x 792 points

# Clause layout on the MOU page: bbox = [left, top, right, bottom] (origin bottom-left).
MOU_CLAUSES = [
    ("9.1", [60, 690, 552, 620],
     "Section 9.1: Employees in the Public Works maintenance classification who are "
     "mandated to work on a recognized County Holiday shall receive their base hourly "
     "rate, plus a holiday premium of 1.5x their base rate for all hours worked."),
    ("9.2", [60, 590, 552, 500],
     "Section 9.2 - Shift Differential Exception: If an employee's regular shift already "
     "falls on a weekend, and the holiday lands on that weekend, they do not receive the "
     "1.5x holiday premium. Instead, they receive a flat $150 inconvenience bonus for the "
     "shift, paid at straight time."),
    ("9.3", [60, 470, 552, 410],
     "Section 9.3 - Bilingual Premium: Employees holding an active Bilingual Certification "
     "receive a 5% bump to their base hourly rate. This 5% must be factored into the base "
     "rate before the 1.5x holiday multiplier is applied."),
]


def _wrap(text, width=95):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def make_mou(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(60, 740, "SEIU MOU — Article 9: Overtime & Holidays")
    c.setFont("Helvetica", 10)
    clauses_out = []
    for clause, bbox, text in MOU_CLAUSES:
        left, top, right, bottom = bbox
        y = top - 12
        for line in _wrap(text):
            c.drawString(left + 4, y, line)
            y -= 14
        clauses_out.append({"clause": clause, "text": text, "page": 1,
                            "bbox": bbox, "char_span": [0, len(text)]})
    c.showPage()
    c.save()
    _sidecar(path, clauses_out,
             "SEIU MOU Article 9 governs overtime and holiday pay for Public Works "
             "maintenance crews, including holiday premiums, weekend shift-differential "
             "exceptions, and bilingual premiums.")


def make_salary(path):
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(60, 740, "Public Works Salary Schedule FY26")
    c.setFont("Helvetica", 10)
    rows = [
        "Classification        Step 1      Step 2      Step 3",
        "Road Tech I           $28.00      $30.00      $32.00",
        "Road Tech II          $33.00      $35.00      $37.00",
        "Road Tech III         $38.00      $40.00      $42.00",
    ]
    y = 700
    for r in rows:
        c.drawString(64, y, r)
        y -= 18
    text = ("This document is the FY26 salary schedule listing base hourly rates by "
            "classification and step for Public Works. It contains pay rates only and "
            "no overtime, holiday, or premium rules.")
    clauses = [{"clause": "", "text": text, "page": 1,
                "bbox": [60, 700, 552, 620], "char_span": [0, len(text)]}]
    c.showPage()
    c.save()
    _sidecar(path, clauses,
             "FY26 salary schedule: base hourly rates by classification and step for "
             "Public Works. Pay rates only; no overtime or holiday rules.")


def _sidecar(pdf_path, clauses, summary):
    base, _ = os.path.splitext(pdf_path)
    with open(base + ".clauses.json", "w") as f:
        json.dump({"text": "\n".join(c["text"] for c in clauses),
                   "summary_hint": summary, "clauses": clauses}, f, indent=2)


if __name__ == "__main__":
    os.makedirs(SRC, exist_ok=True)
    make_mou(os.path.join(SRC, "mou_article9.pdf"))
    make_salary(os.path.join(SRC, "salary_schedule.pdf"))
    print("wrote reference PDFs + clause sidecars to", os.path.normpath(SRC))
