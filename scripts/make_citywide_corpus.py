"""Generate a realistic multi-department document CORPUS for the citywide demo.

~15 documents across 5 bargaining units, so retrieval must actually identify candidates
from metadata + summary rather than "the only PDF in the folder":

  police          POA MOU 2024-27 (large) · POA Side Letter 2025 · Police Salary Schedule
  public-works    SEIU MOU 2024-27 · SEIU MOU 2021-24 (EXPIRED — proves date routing)
                  · PW Salary Schedule
  sheriff         Corrections MOU 2025-28 · Sheriff Uniform Policy
  fire            IAFF MOU 2024-27 · Fire Salary Schedule
  citywide        Personnel Rules · Travel & Expense Policy · Tuition Reimbursement
                  · Employee Handbook Excerpt

Deliberate traps for retrieval:
  - every unit has a "holiday" and an "overtime" clause -> keyword alone is ambiguous
  - two versions of the SEIU MOU -> only effective dates disambiguate
  - salary schedules mention pay but contain no rules
"""
import os
import shutil
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SRC = os.path.join(os.path.dirname(__file__), "..", "cases", "citywide", "sources")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=13, spaceBefore=14,
                    spaceAfter=6, textColor=colors.HexColor("#1f3a5f"))
SEC = ParagraphStyle("SEC", parent=styles["Normal"], fontSize=10, leading=14.5,
                     spaceAfter=9, alignment=4)
TITLE = ParagraphStyle("T", parent=styles["Title"], fontSize=17, spaceAfter=4)
SUB = ParagraphStyle("SUB", parent=styles["Normal"], fontSize=10.5, alignment=1,
                     textColor=colors.HexColor("#4a5568"), spaceAfter=16)

BOILER = (" The parties agree this section was reached through good-faith negotiation under "
          "the Meyers-Milias-Brown Act, that nothing herein waives any right afforded by "
          "state or federal law, and that disputes arising under it are subject to the "
          "grievance procedure. Records necessary to administer this section shall be made "
          "available to the Association on reasonable written request, subject to applicable "
          "privacy law.")


def doc(filename, title, subtitle, articles, tables=None):
    """articles: [(heading, [(section_no, text), ...])]"""
    path = os.path.join(SRC, filename)
    d = SimpleDocTemplate(path, pagesize=letter, leftMargin=1 * inch, rightMargin=1 * inch,
                          topMargin=1 * inch, bottomMargin=1 * inch, title=title)
    story = [Paragraph(title, TITLE), Paragraph(subtitle, SUB)]
    for heading, sections in articles:
        story.append(Paragraph(heading, H1))
        for num, text in sections:
            story.append(Paragraph(f"<b>Section {num}.</b> {text}{BOILER}", SEC))
    for cap, rows in (tables or []):
        story.append(Paragraph(cap, H1))
        t = Table(rows, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f7")]),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))
    d.build(story)
    return filename


# --------------------------------------------------------------------------- #
# FIRE — IAFF MOU
# --------------------------------------------------------------------------- #
FIRE = [("Article 4 — Hours & Shift Schedule", [
    ("4.1", "Suppression personnel shall work a 48/96 schedule consisting of two consecutive "
     "24-hour shifts followed by 96 hours off, averaging fifty-six (56) hours per week."),
    ("4.2", "Trades of shift between employees of equal rank are permitted with prior "
     "approval of the Battalion Chief and shall not incur overtime liability for the City.")]),
    ("Article 5 — Overtime & Holiday Pay", [
        ("5.1", "Overtime shall be paid at one and one-half (1.5) times the regular rate for "
         "all hours worked beyond the regularly scheduled shift, computed on the FLSA 7(k) "
         "work period of twenty-four (24) days."),
        ("5.2", "Suppression personnel required to work on a recognized holiday shall receive "
         "holiday pay equal to two (2) times the base hourly rate for all hours worked, in "
         "recognition of the continuous nature of fire suppression operations."),
        ("5.3", "Mandatory recall to a working incident shall be compensated at a minimum of "
         "three (3) hours at the overtime rate.")]),
    ("Article 6 — Specialty Assignments", [
        ("6.1", "Paramedic Certification. Firefighters holding a current State paramedic "
         "license and assigned to a medic unit shall receive a fifteen percent (15%) premium "
         "above base hourly rate."),
        ("6.2", "Hazardous Materials Technician. Personnel assigned to the HazMat team shall "
         "receive a five percent (5%) premium above base hourly rate."),
        ("6.3", "Apparatus Engineer. Personnel assigned as Apparatus Engineer shall receive a "
         "seven and one-half percent (7.5%) premium above base hourly rate.")]),
    ("Article 9 — Leaves", [
        ("9.1", "Bereavement Leave. Employees shall receive up to four (4) shifts of paid "
         "bereavement leave upon the death of an immediate family member."),
        ("9.2", "Vacation accrues at the rate of 12 hours per pay period for suppression "
         "personnel, reflecting the 56-hour work week.")])]

# --------------------------------------------------------------------------- #
# PUBLIC WORKS — SEIU 2024. The corpus's reference costing document: three golden
# cases depend on these exact numbers (1.5x premium, $150 weekend bonus, 5% bilingual).
# Changing the text here changes the expected totals in case.yaml.
# --------------------------------------------------------------------------- #
SEIU_2024 = [
    ("9.1", "Employees in the Public Works maintenance classification who are mandated to "
     "work on a recognized County Holiday shall receive their base hourly rate, plus a "
     "holiday premium of 1.5x their base rate for all hours worked."),
    ("9.2", "Shift Differential Exception. If an employee's regular shift already falls on "
     "a weekend, and the holiday lands on that weekend, they do not receive the 1.5x "
     "holiday premium. Instead, they receive a flat $150 inconvenience bonus for the "
     "shift, paid at straight time."),
    ("9.3", "Bilingual Premium. Employees holding an active Bilingual Certification receive "
     "a 5% bump to their base hourly rate. This 5% must be factored into the base rate "
     "before the 1.5x holiday multiplier is applied."),
]

# --------------------------------------------------------------------------- #
# SHERIFF — corrections. Deliberately UNSATISFIABLE for costing: Article 12 pays for a
# "holdover on a High-Security Day", and no roster fact says whether a holdover happened.
# The draft contract must therefore REFUSE to draft these rather than invent when: True.
# That refusal is a demonstrated feature (PRD §6.3), not a corpus defect — do not "fix"
# it by adding a holdover column to the roster.
# --------------------------------------------------------------------------- #
SHERIFF_2025 = [
    ("12.1", "Correctional officers who are mandated to work a holdover past their "
     "scheduled shift on a designated High-Security Day shall receive their base hourly "
     "rate, plus a holdover premium of 1.0x their base rate for all held-over hours."),
    ("12.2", "Graveyard Exception. If an officer's regular assignment is the graveyard "
     "shift, they do not receive the 1.0x holdover premium. Instead, they receive a flat "
     "$200 night-holdover stipend for the shift, paid at straight time."),
    ("12.3", "Hazard Premium. Officers holding an active Hazmat Certification receive an 8% "
     "bump to their base hourly rate. This 8% must be factored into the base rate before "
     "the holdover multiplier is applied."),
]

# --------------------------------------------------------------------------- #
# CITYWIDE POLICIES
# --------------------------------------------------------------------------- #
PERSONNEL = [("Article 2 — Employment Categories", [
    ("2.1", "Regular full-time employees are appointed to authorized positions and work a "
     "minimum of forty (40) hours per week. Part-time employees work fewer than thirty (30) "
     "hours per week and are not eligible for health benefits."),
    ("2.2", "Probationary Period. All new employees serve a twelve (12) month probationary "
     "period unless a Memorandum of Understanding provides otherwise, during which employment "
     "is at will.")]),
    ("Article 6 — Attendance & Leave (Unrepresented)", [
        ("6.1", "Unrepresented and management employees accrue vacation at 6.15 hours per pay "
         "period (160 hours annually), with a maximum accrual of 320 hours."),
        ("6.2", "Bereavement Leave. Unrepresented employees shall receive up to three (3) "
         "working days of paid bereavement leave. Represented employees are governed by their "
         "applicable Memorandum of Understanding."),
        ("6.3", "Jury Duty. Employees summoned for jury duty shall receive their regular pay "
         "for up to ten (10) working days.")]),
    ("Article 11 — Conflict of Interest", [
        ("11.1", "Employees shall not engage in outside employment that conflicts with the "
         "performance of City duties or that involves the use of City equipment.")])]

TRAVEL = [("Article 1 — Travel Authorization", [
    ("1.1", "All out-of-town travel requires prior written approval from the Department Head "
     "and, for travel exceeding $1,500, from the City Manager."),
    ("1.2", "Mileage. Use of a personal vehicle for City business shall be reimbursed at the "
     "prevailing IRS standard mileage rate, published annually.")]),
    ("Article 2 — Per Diem & Lodging", [
        ("2.1", "Meal per diem shall be reimbursed at seventy-five dollars ($75) per full day "
         "of travel, prorated for partial days at breakfast $15, lunch $20, dinner $40."),
        ("2.2", "Lodging shall be reimbursed at actual cost not to exceed the GSA rate for the "
         "destination city. Receipts are required for all lodging.")])]

TUITION = [("Article 1 — Tuition Reimbursement", [
    ("1.1", "The City shall reimburse eligible employees up to two thousand five hundred "
     "dollars ($2,500) per fiscal year for approved coursework at an accredited institution, "
     "completed with a grade of C or better."),
    ("1.2", "Coursework must be job-related or part of an approved degree program. Advance "
     "written approval from the Department Head and Human Resources is required."),
    ("1.3", "An employee who separates from City service within twelve (12) months of "
     "receiving reimbursement shall repay a prorated amount.")])]

HANDBOOK = [("Section 3 — Workplace Conduct", [
    ("3.1", "The City maintains a zero-tolerance policy for harassment, discrimination, and "
     "retaliation, consistent with FEHA and Title VII."),
    ("3.2", "Dress Code. Employees shall dress in a manner appropriate to their duties. "
     "Represented employees issued uniforms are governed by their applicable MOU.")]),
    ("Section 7 — Technology Use", [
        ("7.1", "City-issued devices remain City property and are subject to inspection. "
         "Employees have no expectation of privacy in data stored on City systems.")])]


def salary_table(rows):
    return [["Classification", "Step A", "Step B", "Step C", "Step D", "Step E"]] + rows


def build():
    os.makedirs(SRC, exist_ok=True)
    made = []

    made.append(doc("fire_iaff_mou_2024.pdf",
                    "City of Sand City — IAFF Local 3535",
                    "Memorandum of Understanding — Fire Suppression Unit<br/>"
                    "July 1, 2024 through June 30, 2027", FIRE))

    made.append(doc("personnel_rules.pdf", "City of Sand City",
                    "Personnel Rules & Regulations — Unrepresented & Management Employees<br/>"
                    "Adopted January 2024", PERSONNEL))

    made.append(doc("travel_expense_policy.pdf", "City of Sand City",
                    "Administrative Policy 04 — Travel &amp; Expense Reimbursement<br/>"
                    "Effective January 1, 2024 — applies to all employees", TRAVEL))

    made.append(doc("tuition_reimbursement_policy.pdf", "City of Sand City",
                    "Administrative Policy 09 — Tuition Reimbursement<br/>"
                    "Effective January 1, 2024 — applies to all employees", TUITION))

    made.append(doc("employee_handbook.pdf", "City of Sand City",
                    "Employee Handbook (Excerpt) — All Employees<br/>Revised 2024", HANDBOOK))

    # Salary schedules — mention pay, contain no rules (retrieval must not pick these)
    made.append(doc("police_salary_schedule.pdf", "City of Sand City",
                    "Police Salary Schedule FY26 — Effective July 1, 2025", [], tables=[
                        ("Appendix A — Sworn Police Hourly Rates", salary_table([
                            ["Police Officer", "$42.00", "$44.10", "$46.30", "$48.00", "$50.40"],
                            ["Corporal", "$47.00", "$49.35", "$51.80", "$52.00", "$56.10"],
                            ["Sergeant", "$53.00", "$55.65", "$58.00", "$61.35", "$64.40"]]))]))

    made.append(doc("publicworks_salary_schedule.pdf", "City of Sand City",
                    "Public Works Salary Schedule FY26 — Effective July 1, 2025", [], tables=[
                        ("Appendix A — Public Works Hourly Rates", salary_table([
                            ["Road Tech I", "$28.00", "$30.00", "$32.00", "$34.00", "$36.00"],
                            ["Road Tech II", "$33.00", "$35.00", "$37.00", "$39.00", "$41.00"],
                            ["Road Supervisor", "$40.00", "$42.00", "$44.00", "$46.00", "$48.00"]]))]))

    made.append(doc("fire_salary_schedule.pdf", "City of Sand City",
                    "Fire Salary Schedule FY26 — Effective July 1, 2025", [], tables=[
                        ("Appendix A — Fire Suppression Hourly Rates", salary_table([
                            ["Firefighter", "$38.00", "$39.90", "$41.90", "$44.00", "$46.20"],
                            ["Fire Engineer", "$43.00", "$45.15", "$47.40", "$49.80", "$52.30"],
                            ["Fire Captain", "$50.00", "$52.50", "$55.10", "$57.90", "$60.80"]]))]))

    # Amendment / side letter — same unit as the POA MOU, later effective date
    made.append(doc("poa_side_letter_2025.pdf", "City of Sand City & Police Officers Association",
                    "Side Letter Agreement No. 2025-01 — Effective July 1, 2025",
                    [("Side Letter 2025-01 — Graveyard Differential", [
                        ("1.1", "Notwithstanding Section 6.1 of the 2024-2027 Memorandum of "
                         "Understanding, effective July 1, 2025 the graveyard shift differential "
                         "for sworn police personnel is increased from five and one-half percent "
                         "(5.5%) to six and one-half percent (6.5%) of base hourly rate."),
                        ("1.2", "All other terms of Article 6 remain in full force and effect. "
                         "This Side Letter expires with the underlying Memorandum of "
                         "Understanding on June 30, 2027.")])]))

    # Superseded SEIU MOU — only effective dates distinguish it from the current one
    made.append(doc("seiu_mou_2021_expired.pdf", "City of Sand City — SEIU Local 521",
                    "Memorandum of Understanding — Public Works Maintenance Unit<br/>"
                    "July 1, 2021 through June 30, 2024 (SUPERSEDED)",
                    [("Article 9 — Overtime & Holidays (2021-2024 terms)", [
                        ("9.1", "Employees in the Public Works maintenance classification who are "
                         "mandated to work on a recognized County Holiday shall receive their base "
                         "hourly rate, plus a holiday premium of one and one-quarter (1.25) times "
                         "their base rate for all hours worked."),
                        ("9.2", "Shift Differential Exception. Employees whose regular shift falls "
                         "on a weekend receive a flat one hundred dollar ($100) inconvenience bonus "
                         "in lieu of the holiday premium."),
                        ("9.3", "Bilingual Premium. Certified bilingual employees receive a three "
                         "percent (3%) bump to base hourly rate.")])]))
    # ---- SEIU 2024 (public works) — the corpus's reference costing document ----
    made.append(doc("seiu_mou_2024.pdf",
                    "City of Sand City — SEIU Local 521",
                    "Public Works Maintenance Unit · MOU 2024–2027 · Article 9",
                    [("Article 9 — Overtime & Holiday Pay", SEIU_2024)]))

    # ---- SHERIFF corrections ----
    made.append(doc("sheriff_mou_2025.pdf",
                    "County of Sand City — Correctional Officers Association",
                    "Corrections Unit · MOU 2025–2028 · Article 12",
                    [("Article 12 — Holdover & Premiums", SHERIFF_2025)]))

    # ---- POLICE (POA) — the 26-page blind-test MOU, generated by its own script ----
    # Copied rather than duplicated: cases/sandcity is the standalone blind case and the
    # single source of this document. Two copies would drift.
    poa_src = os.path.join(os.path.dirname(__file__), "..", "cases", "sandcity",
                           "sources", "sandcity_poa_mou.pdf")
    if not os.path.exists(poa_src):
        import subprocess
        subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__),
                                                     "make_sandcity_mou.py")], check=True)
    shutil.copy2(poa_src, os.path.join(SRC, "sandcity_poa_mou.pdf"))
    made.append("sandcity_poa_mou.pdf (copied from cases/sandcity)")

    print(f"generated {len(made)} documents in {os.path.normpath(SRC)}")
    for m in made:
        print("   ", m)

    declared = _declared_sources()
    on_disk = {f for f in os.listdir(SRC) if f.endswith(".pdf")}
    missing = {os.path.basename(s["file"]) for s in declared} - on_disk
    if missing:
        # case.yaml is the contract. A document it declares but nobody generates is a
        # corpus that cannot be rebuilt from source — which is how three of these went
        # missing in the first place, and how a container build would fail at ingest.
        print(f"\nERROR: case.yaml declares documents this script does not build: "
              f"{sorted(missing)}", file=sys.stderr)
        raise SystemExit(1)
    print(f"\nall {len(declared)} documents declared in case.yaml are present")


def _declared_sources():
    import yaml
    with open(os.path.join(SRC, "..", "case.yaml")) as f:
        return yaml.safe_load(f)["sources"]


if __name__ == "__main__":
    build()
