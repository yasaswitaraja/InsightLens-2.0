import io
import json
import pytest
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

from backend.loaders import load_file_documents


def make_docx():
    doc = Document()
    doc.add_heading("InsightLens Test", level=1)
    doc.add_paragraph("This is a sample DOCX document.")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Topic"
    table.cell(0, 1).text = "Status"
    table.add_row().cells[0].text = "AI"
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def make_xlsx():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Test Data"
    sheet.append(["Topic", "Score"])
    sheet.append(["Machine Learning", 95])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def make_pptx():
    presentation = Presentation()
    slide = presentation.slides.add_slide(
        presentation.slide_layouts[1]
    )
    slide.shapes.title.text = "InsightLens Presentation"
    slide.placeholders[1].text = "Testing presentation extraction."
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


samples = [
    ("sample.txt", b"Testing plain text extraction."),
    ("sample.md", b"# InsightLens\n\nTesting Markdown extraction."),
    ("sample.csv", b"Name,Score\nAlice,95\nBob,88\n"),
    ("sample.docx", make_docx()),
    ("sample.xlsx", make_xlsx()),
    ("sample.pptx", make_pptx()),
    ("sample.py", b"def hello():\n    return 'InsightLens test'\n"),
    (
        "sample.ipynb",
        json.dumps({
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["print('Notebook extraction works')"],
                    "metadata": {},
                    "outputs": [],
                    "execution_count": None,
                }
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }).encode("utf-8"),
    ),
]


@pytest.mark.parametrize(
    "filename,content",
    samples,
    ids=[sample[0] for sample in samples],
)
def test_load_file_documents(filename, content):
    documents = load_file_documents(content, filename)

    assert documents, f"No documents extracted from {filename}"

    extracted_text = "\n".join(
        str(document.page_content)
        for document in documents
    )

    assert extracted_text.strip(), f"Empty extracted text for {filename}"

    for document in documents:
        for key, value in document.metadata.items():
            assert isinstance(value, (str, int, float, bool)), (
                f"Unsupported metadata in {filename}: "
                f"{key}={type(value).__name__}"
            )
