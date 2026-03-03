"""
Script to generate a sample new tender questionnaire Excel file.
Run: python data/create_sample_tender.py
"""
import openpyxl
from pathlib import Path

questions = [
    "Do you support SSL and TLS encryption for data in transit?",
    "Does the platform enforce TLS 1.2 or higher for all communications?",
    "Please describe your information security management framework.",
    "Do you hold ISO 9001 or equivalent quality certification?",
    "What is your approach to business continuity and disaster recovery planning?",
    "How do you assess and manage risks from third-party suppliers?",
    "What environmental sustainability commitments does your organisation hold?",
    "How does your organisation promote equality, diversity and inclusion?",
    "What measurable social value can you deliver as part of this contract?",
    "How is your organisation governed and what oversight mechanisms are in place?",
    "Can you provide evidence of relevant experience delivering comparable contracts?",
    "What is your pricing model and how do you ensure cost transparency?",
    "How do you handle data subject access requests under GDPR?",
    "Describe your software development lifecycle and quality assurance processes.",
    "What AI or machine learning capabilities does your platform offer?",
]

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Tender Questions"
ws.append(["QUESTION"])
for q in questions:
    ws.append([q])

out_path = Path(__file__).parent / "sample_new_tender.xlsx"
wb.save(out_path)
print(f"Created: {out_path}")
