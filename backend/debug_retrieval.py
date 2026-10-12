
import os
from dotenv import load_dotenv
from pipeline import InsightEngine
from loaders import load_pdf_documents

load_dotenv()

pdf_path = r"C:\Users\Yasaswita\Downloads\Attention is all you need.pdf"

with open(pdf_path, "rb") as pdf_file:
    pdf_content = pdf_file.read()

engine = InsightEngine()

documents = load_pdf_documents(
    pdf_content,
    "Attention is all you need.pdf",
)

engine._add_documents(
    documents,
    "Attention is all you need.pdf",
    "pdf",
)

questions = [
    "Explain Section 3.1 Encoder and Decoder Stacks.",
    "How many identical layers does the encoder contain?",
    "What are the two sub-layers in each encoder layer?",
]

for question in questions:
    print("\n" + "=" * 70)
    print("QUESTION:", question)

    results = engine.vector_store.similarity_search(
        question,
        k=5,
    )

    for rank, document in enumerate(results, start=1):
        print(f"\nResult {rank}")
        print("Chunk ID:", document.metadata.get("chunk_id"))
        print("Page:", document.metadata.get("page"))
        print(document.page_content[:700])
