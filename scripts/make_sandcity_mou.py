"""Generate a realistic, LARGE municipal police MOU PDF for testing.

City of Sand City — Police Officers Association (POA), 2024–2027. ~30-40 pages across
21 articles + 3 appendices, so the demo exercises the real large-document path: async
docling ingest, hybrid retrieval over hundreds of clauses, Policy Q&A across many
topics, and costing on the pay articles. Docling extracts clauses + bounding boxes at
ingest, so no sidecar is needed.

Key clause numbers referenced by cases/sandcity/rules/rules_ratified.json:
  §3.3 bilingual · §5.2 holiday premium · §6.1 graveyard differential
"""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

OUT = os.path.join(os.path.dirname(__file__), "..", "cases", "sandcity", "sources",
                   "sandcity_poa_mou.pdf")

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=13, spaceBefore=16,
                    spaceAfter=6, textColor=colors.HexColor("#1f3a5f"))
SEC = ParagraphStyle("SEC", parent=styles["Normal"], fontSize=10, leading=14.5,
                     spaceAfter=9, alignment=4)
TITLE = ParagraphStyle("T", parent=styles["Title"], fontSize=18, spaceAfter=6)
SUB = ParagraphStyle("SUB", parent=styles["Normal"], fontSize=11, alignment=1,
                     textColor=colors.HexColor("#4a5568"), spaceAfter=18)

BOILER = (" The parties acknowledge that this provision was reached through good-faith "
          "negotiation pursuant to the Meyers-Milias-Brown Act. Nothing in this section "
          "shall be construed to waive any right afforded by state or federal law. Any "
          "dispute arising under this section shall be subject to the grievance procedure "
          "set forth in Article 12. Where a conflict exists between this section and a "
          "departmental policy, this Memorandum of Understanding shall control.")

# Real MOUs are dense with repetitive legalese. Varied boilerplate is appended to each
# section (cycled) so the document reaches realistic length without duplicating identical
# text everywhere, which would distort retrieval.
BOILERS = [
    "The parties further agree that implementation of this section shall not result in a "
    "reduction of any benefit in effect on the effective date of this Agreement, and that "
    "any ambiguity shall be resolved in a manner consistent with past practice where such "
    "practice is documented, mutually known, and consistently applied. The City shall "
    "provide the Association with not less than fourteen (14) calendar days' written notice "
    "prior to implementing any change in the administration of this section, and shall upon "
    "request meet and confer regarding the impacts of such change.",

    "Records necessary to administer this section shall be maintained by the Department and "
    "made available to the Association upon reasonable written request, subject to the "
    "privacy protections of the California Public Records Act and the Information Practices "
    "Act. Nothing in this section obligates either party to disclose materials protected by "
    "the attorney-client privilege, the attorney work-product doctrine, or the official "
    "information privilege.",

    "The Association expressly reserves all rights afforded under Government Code section "
    "3500 et seq. Neither the exercise nor the non-exercise of any right under this section "
    "shall constitute a waiver of that right, nor shall any past practice inconsistent with "
    "the express terms of this section be deemed to modify those terms. The parties agree "
    "that this section shall be interpreted in a manner consistent with the Fair Labor "
    "Standards Act where applicable.",

    "In the event that any federal or state statute, regulation, or judicial decision "
    "materially affects the operation of this section, the parties shall meet within thirty "
    "(30) calendar days to negotiate conforming amendments. Pending such negotiation, the "
    "affected provision shall be administered in the manner most consistent with the parties' "
    "original intent, to the extent permitted by law.",

    "Payments and benefits under this section shall be administered through the City's "
    "regular payroll process and reported to CalPERS only to the extent such compensation is "
    "reportable as special compensation under Title 2 CCR section 571 or its successor. The "
    "parties make no representation as to the pensionability of any item of compensation, and "
    "any determination by CalPERS shall control.",

    "Supervisors shall administer this section uniformly and without discrimination on the "
    "basis of any protected classification. Alleged violations shall be addressed through the "
    "grievance procedure, provided that nothing herein shall limit an employee's right to "
    "pursue statutory remedies before the appropriate administrative agency or court of "
    "competent jurisdiction.",
]

ARTICLES = [
    ("Article 1 — Recognition & Scope", [
        ("1.1", "The City of Sand City recognizes the Sand City Police Officers Association "
         "(POA) as the exclusive bargaining representative for all sworn personnel below the "
         "rank of Lieutenant, including the classifications of Police Officer, Corporal, and "
         "Sergeant, for the term of this Memorandum of Understanding (MOU), July 1, 2024 "
         "through June 30, 2027. Recognition extends to all matters within the scope of "
         "representation, including wages, hours, and other terms and conditions of "
         "employment." + BOILER),
        ("1.2", "This MOU constitutes the entire agreement between the parties and supersedes "
         "all prior agreements, side letters, memoranda, and past practices except where "
         "expressly incorporated by reference. No amendment shall be binding unless reduced "
         "to writing and signed by authorized representatives of both parties."),
        ("1.3", "Should any provision of this MOU be held invalid by a court of competent "
         "jurisdiction, the remainder shall continue in full force and effect, and the parties "
         "shall meet within thirty (30) days to negotiate a replacement provision."),
    ]),
    ("Article 2 — Definitions", [
        ("2.1", "'Base Hourly Rate' means the employee's hourly rate of pay as set forth in "
         "the salary schedule in Appendix A, exclusive of any premium, differential, "
         "incentive, or allowance, and before application of any multiplier."),
        ("2.2", "'Regular Rate of Pay' means the base hourly rate plus those premiums required "
         "by the Fair Labor Standards Act to be included in the regular rate for purposes of "
         "computing overtime."),
        ("2.3", "'Immediate Family' means a spouse, registered domestic partner, child, "
         "stepchild, parent, stepparent, sibling, grandparent, grandchild, or the equivalent "
         "relation of the employee's spouse or registered domestic partner."),
        ("2.4", "'Shift' means the continuous period of assigned duty. 'Day Shift' means 0600 "
         "to 1400 hours; 'Swing Shift' means 1400 to 2200 hours; 'Graveyard Shift' means 2200 "
         "to 0600 hours."),
        ("2.5", "'Seniority' means continuous service in a sworn classification with the City, "
         "measured from the date of sworn appointment, excluding unpaid leaves in excess of "
         "thirty (30) consecutive days."),
    ]),
    ("Article 3 — Salary & Wages", [
        ("3.1", "Base Salary Schedule. Employees shall be compensated according to the salary "
         "schedule set forth in Appendix A, consisting of five steps (A through E) per "
         "classification. Advancement of one step shall occur upon each year of satisfactory "
         "service until the top step is reached, subject to a satisfactory performance "
         "evaluation on file." + BOILER),
        ("3.2", "Cost of Living Adjustment (COLA). Effective the first full pay period in July "
         "of each year of this agreement, base salaries shall increase by three percent (3.0%) "
         "in 2024, three percent (3.0%) in 2025, and three and one-half percent (3.5%) in "
         "2026. COLA increases shall apply to all steps of the salary schedule."),
        ("3.3", "Bilingual Premium. Employees certified as bilingual by the Department and "
         "regularly assigned to use a second language in the course of their duties shall "
         "receive a five percent (5%) premium added to their base hourly rate. This premium "
         "shall be factored into the base rate before any shift differential or holiday "
         "multiplier is applied. Certification shall be re-tested every three (3) years."),
        ("3.4", "Acting Pay. An employee assigned in writing to perform the duties of a higher "
         "classification for more than five (5) consecutive shifts shall receive the base rate "
         "of the higher classification for all hours so assigned, retroactive to the first "
         "shift of the assignment."),
        ("3.5", "Payday. Employees shall be paid bi-weekly, twenty-six (26) times per year. "
         "Each bi-weekly pay period consists of eighty (80) regularly scheduled hours."),
    ]),
    ("Article 4 — Overtime & Callback", [
        ("4.1", "Overtime. All hours worked in excess of the regularly scheduled shift, or in "
         "excess of forty (40) hours in a designated work week, shall be compensated at one "
         "and one-half (1.5) times the employee's regular rate of pay. Overtime must be "
         "authorized in advance by a supervisor except in exigent circumstances." + BOILER),
        ("4.2", "Court Time. An employee required to appear in court during off-duty hours "
         "shall receive a minimum of three (3) hours of pay at the overtime rate, regardless "
         "of the actual time spent, and shall be compensated for actual time in excess of "
         "three hours."),
        ("4.3", "Callback. An employee called back to duty after completing a shift and having "
         "left the workplace shall receive a minimum of four (4) hours of pay at the overtime "
         "rate. Callback pay shall not apply to an employee held over contiguous with a "
         "scheduled shift."),
        ("4.4", "Compensatory Time. In lieu of cash overtime, an employee may elect "
         "compensatory time off at the rate of one and one-half (1.5) hours for each overtime "
         "hour worked, accrued to a maximum of one hundred twenty (120) hours."),
        ("4.5", "Standby. An employee placed on standby shall receive two (2) hours of straight "
         "time pay for each twenty-four (24) hour period of standby assignment."),
    ]),
    ("Article 5 — Holidays & Holiday Pay", [
        ("5.1", "Recognized Holidays. The City recognizes twelve (12) paid holidays per year as "
         "listed in Appendix B, including New Year's Day, Martin Luther King Jr. Day, "
         "Presidents' Day, Memorial Day, Independence Day, Labor Day, Veterans Day, "
         "Thanksgiving Day and the day following, and Christmas Day."),
        ("5.2", "Holiday Work Premium. Sworn personnel mandated to work on a recognized City "
         "holiday shall receive their base hourly rate plus a holiday premium of one and "
         "one-half (1.5) times their base rate for all hours worked on that holiday, for a "
         "total of two and one-half (2.5) times the base rate. The holiday premium shall be "
         "applied after any applicable shift differential or bilingual premium has been "
         "incorporated into the base rate." + BOILER),
        ("5.3", "Holiday in Lieu. Personnel whose regular day off falls on a recognized holiday "
         "shall receive an alternate paid day off, to be scheduled by mutual agreement within "
         "ninety (90) days of the holiday."),
        ("5.4", "Holiday Bank. Employees assigned to shifts that operate continuously may elect "
         "to bank holiday hours, to a maximum of ninety-six (96) hours, payable upon "
         "separation at the employee's then-current base rate."),
    ]),
    ("Article 6 — Shift Differentials", [
        ("6.1", "Graveyard Differential. Personnel assigned to the graveyard shift (2200 to "
         "0600 hours) shall receive a shift differential of five and one-half percent (5.5%) "
         "applied to their base hourly rate for all hours worked on that shift. The "
         "differential shall be applied to base pay before any holiday multiplier is "
         "calculated." + BOILER),
        ("6.2", "Swing Differential. Personnel assigned to the swing shift (1400 to 2200 hours) "
         "shall receive a shift differential of three percent (3.0%) applied to their base "
         "hourly rate for all hours worked on that shift."),
        ("6.3", "Stacking Limitation. Shift differentials apply to base pay only and shall not "
         "be applied to flat-dollar premiums such as the Field Training Officer premium or the "
         "K-9 handler premium. Under no circumstances shall a percentage differential be "
         "applied to a flat-dollar premium."),
        ("6.4", "Eligibility. A shift differential is payable only for hours actually worked on "
         "the qualifying shift and shall not be paid during paid leave, vacation, or "
         "compensatory time off."),
    ]),
    ("Article 7 — Specialty & Premium Pay", [
        ("7.1", "Field Training Officer (FTO). Officers actively assigned to train new recruits "
         "shall receive a flat premium of two hundred fifty dollars ($250) per bi-weekly pay "
         "period. The FTO premium is separate from base pay and is not subject to shift "
         "differentials or holiday multipliers." + BOILER),
        ("7.2", "K-9 Handler. Officers assigned as K-9 handlers shall receive a flat premium of "
         "three hundred dollars ($300) per bi-weekly pay period in recognition of the off-duty "
         "care, feeding, grooming, and maintenance of the assigned animal, which the parties "
         "agree constitutes full compensation for such off-duty work."),
        ("7.3", "Motorcycle Pay. Officers assigned to motorcycle patrol shall receive a flat "
         "premium of one hundred fifty dollars ($150) per bi-weekly pay period."),
        ("7.4", "Detective/Investigator Pay. Officers assigned to the Detective Bureau shall "
         "receive a five percent (5%) premium above their base hourly rate for the duration of "
         "the assignment."),
        ("7.5", "SWAT / Special Response. Officers assigned to the Special Response Team shall "
         "receive a flat premium of one hundred dollars ($100) per bi-weekly pay period, plus "
         "callback pay for actual deployments."),
    ]),
    ("Article 8 — Education Incentive (POST)", [
        ("8.1", "Intermediate POST Certificate. Employees holding a valid Intermediate POST "
         "certificate shall receive an education incentive of five percent (5%) of base salary, "
         "payable each pay period in which the certificate remains valid." + BOILER),
        ("8.2", "Advanced POST Certificate. Employees holding a valid Advanced POST certificate "
         "shall receive an education incentive of seven and one-half percent (7.5%) of base "
         "salary, in lieu of and not in addition to the Intermediate incentive."),
        ("8.3", "Tuition Reimbursement. The City shall reimburse up to two thousand dollars "
         "($2,000) per fiscal year for approved job-related coursework completed with a grade "
         "of C or better, subject to prior written approval by the Chief of Police."),
        ("8.4", "Training Time. Time spent in mandatory training shall be compensated as hours "
         "worked. Voluntary training approved by the Department shall be compensated at the "
         "straight-time rate."),
    ]),
    ("Article 9 — Uniform & Equipment Allowance", [
        ("9.1", "Uniform Allowance. Each sworn employee shall receive an annual uniform "
         "allowance of one thousand two hundred dollars ($1,200), paid in two equal "
         "installments in January and July, for the purchase, cleaning, and maintenance of "
         "required uniforms and safety equipment. The allowance shall be prorated for "
         "employees hired mid-year." + BOILER),
        ("9.2", "Body Armor. The City shall provide and replace ballistic vests on a five-year "
         "cycle or upon manufacturer expiration, whichever occurs first, at no cost to the "
         "employee."),
        ("9.3", "Damaged Property. The City shall repair or replace an employee's personal "
         "property, including prescription eyewear and watches, damaged in the line of duty, "
         "up to five hundred dollars ($500) per incident."),
    ]),
    ("Article 10 — Health & Welfare", [
        ("10.1", "Medical Insurance. The City shall contribute up to one thousand eight hundred "
         "dollars ($1,800) per month toward the employee's selected medical plan for family "
         "coverage, and a proportionate amount for employee-only and employee-plus-one "
         "coverage." + BOILER),
        ("10.2", "Retirement. Employees participate in the CalPERS 3% at 50 formula for classic "
         "members and the applicable PEPRA formula (2.7% at 57) for new members, with employee "
         "contributions as required by law."),
        ("10.3", "Deferred Compensation. The City shall match employee contributions to the "
         "457(b) deferred compensation plan up to one percent (1%) of base salary."),
        ("10.4", "Retiree Medical. Employees retiring with at least twenty (20) years of "
         "service shall receive a City contribution toward retiree medical premiums equal to "
         "the PEMHCA minimum, increased annually per statute."),
        ("10.5", "Life Insurance. The City shall provide term life insurance in an amount equal "
         "to one times the employee's annual base salary."),
    ]),
    ("Article 11 — Leaves of Absence", [
        ("11.1", "Vacation. Employees accrue vacation at the rate of 3.08 hours per pay period "
         "(eighty (80) hours per year) during the first five years of service, increasing "
         "thereafter per the schedule in Appendix C. Maximum accrual is capped at two times "
         "the annual accrual rate." + BOILER),
        ("11.2", "Sick Leave. Employees accrue sick leave at the rate of 3.7 hours per pay "
         "period (ninety-six (96) hours per year), with unlimited accrual. Sick leave may be "
         "used for the employee's own illness or to care for an ill immediate family member."),
        ("11.3", "Bereavement Leave. Employees shall be granted up to five (5) working days of "
         "paid bereavement leave upon the death of an immediate family member as defined in "
         "Section 2.3. An additional two (2) days of accrued leave may be used at the "
         "employee's discretion where travel in excess of five hundred (500) miles is "
         "required."),
        ("11.4", "Family & Medical Leave. Leave shall be administered in accordance with the "
         "Family and Medical Leave Act and the California Family Rights Act, providing up to "
         "twelve (12) weeks of job-protected leave in a rolling twelve-month period."),
        ("11.5", "Military Leave. Military leave shall be granted in accordance with USERRA and "
         "applicable California law."),
        ("11.6", "Association Leave. The Association shall be granted a bank of eighty (80) "
         "hours per fiscal year for use by designated representatives in the conduct of "
         "Association business."),
    ]),
    ("Article 12 — Grievance Procedure", [
        ("12.1", "Definition. A grievance is an alleged violation, misinterpretation, or "
         "misapplication of a specific provision of this Memorandum of Understanding."),
        ("12.2", "Steps. Grievances shall proceed through four steps: (1) the immediate "
         "supervisor, (2) the Division Commander, (3) the Chief of Police, and (4) binding "
         "arbitration before a neutral arbitrator selected by mutual agreement or by striking "
         "from a list provided by the State Mediation and Conciliation Service." + BOILER),
        ("12.3", "Time Limits. A grievance must be filed in writing within fifteen (15) calendar "
         "days of the event giving rise to it, or within fifteen (15) days of when the employee "
         "reasonably should have known of the event. Failure by the City to respond within the "
         "prescribed time shall advance the grievance to the next step."),
        ("12.4", "Costs. The fees and expenses of the arbitrator shall be shared equally by the "
         "parties. Each party shall bear the cost of its own representation."),
    ]),
    ("Article 13 — Management Rights", [
        ("13.1", "The City retains all rights not expressly limited by this MOU, including but "
         "not limited to the right to direct the workforce, determine staffing levels and "
         "organizational structure, schedule shifts, establish standards of service, and take "
         "disciplinary action for just cause." + BOILER),
        ("13.2", "Nothing herein shall be construed to limit the City's authority to respond to "
         "emergencies as determined in its sole discretion, provided that the exercise of such "
         "authority shall not permanently alter terms within the scope of representation "
         "without meeting and conferring."),
    ]),
    ("Article 14 — Hours of Work & Scheduling", [
        ("14.1", "Work Schedules. The Department may assign 4/10, 3/12.5, or 5/8 work schedules. "
         "Any change to an established schedule requires thirty (30) days' notice and an "
         "opportunity to meet and confer over the impacts." + BOILER),
        ("14.2", "Shift Bidding. Shift assignments shall be bid by seniority within "
         "classification twice per year, subject to the Department's right to assign up to "
         "twenty percent (20%) of positions based on operational need."),
        ("14.3", "Rest Between Shifts. Employees shall receive a minimum of eight (8) hours of "
         "rest between assigned shifts, except in emergencies."),
        ("14.4", "Meal Periods. Employees shall receive an uninterrupted thirty (30) minute paid "
         "meal period during each shift, subject to call for service."),
    ]),
    ("Article 15 — Discipline & Due Process", [
        ("15.1", "Just Cause. No employee shall be disciplined, suspended, demoted, or "
         "discharged except for just cause." + BOILER),
        ("15.2", "Peace Officers Bill of Rights. All investigations and disciplinary actions "
         "shall comply with the Public Safety Officers Procedural Bill of Rights Act "
         "(Government Code §3300 et seq.), including the right to representation."),
        ("15.3", "Personnel Files. Employees shall have the right to review their personnel file "
         "upon reasonable notice and to attach a written response to any adverse material."),
    ]),
    ("Article 16 — Layoff & Seniority", [
        ("16.1", "Order of Layoff. In the event of layoff, employees shall be laid off in "
         "inverse order of seniority within the affected classification, after the separation "
         "of temporary and probationary employees." + BOILER),
        ("16.2", "Recall. Laid-off employees shall be maintained on a recall list for twenty-four "
         "(24) months and recalled in order of seniority as vacancies occur."),
        ("16.3", "Notice. The City shall provide thirty (30) days' written notice of layoff and "
         "shall meet and confer over the impacts."),
    ]),
    ("Article 17 — Safety & Equipment", [
        ("17.1", "The City shall provide and maintain all equipment necessary for the safe "
         "performance of duties, in compliance with Cal/OSHA standards." + BOILER),
        ("17.2", "Take-Home Vehicles. Officers residing within twenty-five (25) miles of City "
         "limits and assigned to specialty units may be authorized a take-home vehicle."),
        ("17.3", "Safety Committee. A joint labor-management safety committee shall meet "
         "quarterly to review incidents and recommend corrective action."),
    ]),
    ("Article 18 — Drug & Alcohol Policy", [
        ("18.1", "Testing shall be conducted only upon reasonable suspicion documented in "
         "writing by a supervisor trained in impairment recognition, or following a critical "
         "incident." + BOILER),
        ("18.2", "An employee who voluntarily discloses a substance abuse problem prior to "
         "testing shall be referred to the Employee Assistance Program without discipline for "
         "the disclosure itself."),
    ]),
    ("Article 19 — Probation & Promotion", [
        ("19.1", "Probation. Newly appointed officers shall serve a probationary period of "
         "eighteen (18) months; promoted employees shall serve twelve (12) months." + BOILER),
        ("19.2", "Promotional Examinations. Promotional processes shall consist of a written "
         "examination, an oral board, and a departmental assessment, weighted 30/40/30."),
        ("19.3", "Eligibility Lists. Promotional eligibility lists shall remain in effect for "
         "twenty-four (24) months."),
    ]),
    ("Article 20 — Outside Employment", [
        ("20.1", "Employees may engage in outside employment with prior written approval of the "
         "Chief of Police, provided such employment does not conflict with duties, exceed "
         "twenty (20) hours per week, or involve the sale of alcohol." + BOILER),
        ("20.2", "Approval may be revoked where outside employment is found to interfere with "
         "the employee's performance or the Department's operations."),
    ]),
    ("Article 21 — Term & Reopeners", [
        ("21.1", "Term. This MOU shall be effective July 1, 2024 and shall remain in full force "
         "and effect through June 30, 2027, and thereafter from year to year unless either "
         "party serves written notice of intent to modify." + BOILER),
        ("21.2", "Reopener. Either party may reopen negotiations on wages (Article 3) and one "
         "additional article of its choosing in the final year of the term."),
    ]),
]

SALARY_ROWS = [
    ["Classification", "Step A", "Step B", "Step C", "Step D", "Step E"],
    ["Police Officer", "$42.00", "$44.10", "$46.30", "$48.00", "$50.40"],
    ["Police Officer (Lateral)", "$44.10", "$46.30", "$48.00", "$50.40", "$52.90"],
    ["Corporal", "$47.00", "$49.35", "$51.80", "$52.00", "$56.10"],
    ["Sergeant", "$53.00", "$55.65", "$58.00", "$61.35", "$64.40"],
    ["Detective", "$46.00", "$48.30", "$50.70", "$53.20", "$55.90"],
    ["K-9 Handler", "$45.00", "$47.25", "$49.60", "$52.10", "$54.70"],
    ["Traffic/Motorcycle", "$45.50", "$47.75", "$50.15", "$52.65", "$55.30"],
    ["School Resource Officer", "$44.00", "$46.20", "$48.50", "$50.90", "$53.45"],
    ["Dispatcher I", "$28.00", "$29.40", "$30.90", "$32.40", "$34.00"],
    ["Dispatcher II", "$31.00", "$32.55", "$34.20", "$35.90", "$37.70"],
    ["Community Service Officer", "$26.00", "$27.30", "$28.70", "$30.10", "$31.60"],
    ["Property & Evidence Tech", "$27.50", "$28.90", "$30.30", "$31.80", "$33.40"],
]

HOLIDAY_ROWS = [
    ["Holiday", "Observed"],
    ["New Year's Day", "January 1"],
    ["Martin Luther King Jr. Day", "Third Monday in January"],
    ["Presidents' Day", "Third Monday in February"],
    ["Cesar Chavez Day", "March 31"],
    ["Memorial Day", "Last Monday in May"],
    ["Juneteenth", "June 19"],
    ["Independence Day", "July 4"],
    ["Labor Day", "First Monday in September"],
    ["Veterans Day", "November 11"],
    ["Thanksgiving Day", "Fourth Thursday in November"],
    ["Day after Thanksgiving", "Fourth Friday in November"],
    ["Christmas Day", "December 25"],
]

ACCRUAL_ROWS = [
    ["Years of Service", "Hours per Pay Period", "Annual Hours"],
    ["0 through 5 years", "3.08", "80"],
    ["6 through 10 years", "4.62", "120"],
    ["11 through 15 years", "5.54", "144"],
    ["16 through 20 years", "6.15", "160"],
    ["21 or more years", "6.77", "176"],
]


def _table(rows):
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f7")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=1 * inch, rightMargin=1 * inch,
                            topMargin=1 * inch, bottomMargin=1 * inch,
                            title="City of Sand City POA MOU 2024-2027")
    story = [Paragraph("City of Sand City", TITLE),
             Paragraph("Memorandum of Understanding<br/>Police Officers Association (POA)<br/>"
                       "July 1, 2024 through June 30, 2027", SUB),
             Spacer(1, 10)]
    n = 0
    for title, sections in ARTICLES:
        story.append(Paragraph(title, H1))
        for num, text in sections:
            # cycle varied legalese so the doc reaches realistic MOU length
            body = (f"{text} {BOILERS[n % len(BOILERS)]} "
                    f"{BOILERS[(n + 3) % len(BOILERS)]}")
            story.append(Paragraph(f"<b>Section {num}.</b> {body}", SEC))
            n += 1

    story.append(Paragraph("Appendix A — Salary Schedule (Effective July 1, 2024)", H1))
    story.append(Paragraph("The following hourly base rates apply to each classification and "
                           "step. Rates are exclusive of premiums, differentials, and "
                           "incentives.", SEC))
    story.append(_table(SALARY_ROWS))
    story.append(Spacer(1, 14))

    story.append(Paragraph("Appendix B — Recognized Holidays", H1))
    story.append(Paragraph("The following twelve (12) holidays are recognized for purposes of "
                           "Article 5.", SEC))
    story.append(_table(HOLIDAY_ROWS))
    story.append(Spacer(1, 14))

    story.append(Paragraph("Appendix C — Vacation Accrual Schedule", H1))
    story.append(Paragraph("Vacation accrues per Section 11.1 according to the following "
                           "schedule.", SEC))
    story.append(_table(ACCRUAL_ROWS))

    doc.build(story)
    print("wrote", os.path.normpath(OUT))


if __name__ == "__main__":
    build()
