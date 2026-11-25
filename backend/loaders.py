import logging
import os
import re
import tempfile
import csv
import io

import requests
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader
from youtube_transcript_api import YouTubeTranscriptApi
from types import SimpleNamespace

def extract_video_id(url: str):
    if not url:
        return None

    patterns = [
        r"(?:youtube\.com/watch\?.*?v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/live/)([A-Za-z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def format_timestamp(seconds: float):
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    remaining_seconds = total_seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{remaining_seconds:02d}"

    return f"{minutes:02d}:{remaining_seconds:02d}"


def youtube_watch_url(video_id: str):
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_timestamp_url(video_id: str, seconds: float = 0):
    return f"{youtube_watch_url(video_id)}&t={int(seconds)}s"


def get_youtube_metadata(video_id: str):
    """Get public YouTube metadata without making analysis depend on it.

    First use YouTube's oEmbed endpoint. If it is unavailable, try yt-dlp.
    If both methods fail, return a safe fallback so transcript analysis can
    still continue.
    """
    watch_url = youtube_watch_url(video_id)
    metadata = {
        "title": None,
        "channel": None,
        "thumbnail_url": None,
    }
    headers = {"User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130 Safari/537.36"
    )
    }
    try:
        response = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": watch_url, "format": "json"},
            timeout=6,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        metadata["title"] = data.get("title")
        metadata["channel"] = data.get("author_name")
        metadata["thumbnail_url"] = data.get("thumbnail_url")
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.warning("YouTube oEmbed metadata retrieval failed: %s", exc)
        # Metadata is helpful for the UI, but must not block transcript work.
        

    if not metadata["title"]:
        try:
            import yt_dlp

            options = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "socket_timeout": 15,
                "retries": 2,
                "extractor_retries": 2,
                "http_headers": headers,
            }
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(watch_url, download=False)

            if info:
                metadata["title"] = info.get("title")
                metadata["channel"] = (
                    info.get("channel")
                    or info.get("uploader")
                    or metadata["channel"]
                )
                metadata["thumbnail_url"] = (
                    info.get("thumbnail") or metadata["thumbnail_url"]
                )
        except Exception as exc:
            # Some videos restrict metadata access; keep the app usable.
            logging.warning("YouTube transcript metadata retrieval failed: %s", exc)

    if metadata.get("title"):
        metadata["title"] = str(metadata["title"]).strip()
    else:
        metadata["title"] = f"YouTube Video ({video_id})"
    if metadata.get("channel"):
        metadata["channel"] = str(metadata["channel"]).strip()

    return metadata


def get_free_api_transcript(url: str):
    """Retrieve YouTube transcripts using the free API."""

    headers = {}

    api_key = os.getenv("FREETRANSCRIPT_API_KEY")

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.get(
        "https://api.freetranscriptapi.com/v1/transcript",
        params={"video_url": url},
        headers=headers,
        timeout=45,
    )

    response.raise_for_status()

    data = response.json()
    segments = data.get("transcript")

    if not isinstance(segments, list) or not segments:
        raise RuntimeError(
            "No transcript available from the free transcript service."
        )

    transcript_items = []

    for segment in segments:
        if not isinstance(segment, dict):
            continue

        text = str(segment.get("text", "")).strip()

        if not text:
            continue

        transcript_items.append(
            SimpleNamespace(
                text=text,
                start=float(segment.get("start", 0)),
                duration=float(segment.get("duration", 0)),
            )
        )

    if not transcript_items:
        raise RuntimeError("The returned transcript was empty.")

    return transcript_items



def get_youtube_transcript(url: str):
    video_id = extract_video_id(url)

    if not video_id:
        raise RuntimeError("Invalid YouTube URL.")

    video_metadata = get_youtube_metadata(video_id)
    video_title = video_metadata["title"]
    transcript = None
    last_error = None

    # First attempt: free managed transcript API.
    try:
        transcript = get_free_api_transcript(url)
    except Exception as exc:
        last_error = exc

    # Fallback: existing YouTube transcript library.
    if transcript is None:
        api = YouTubeTranscriptApi()

        for languages in [["en"], ["en", "hi", "te"], None]:
            try:
                if languages:
                    transcript = api.fetch(
                        video_id,
                        languages=languages,
                    )
                else:
                    transcript = None
                    for item in api.list(video_id):
                        transcript = item.fetch()
                        break

                    if transcript is None:
                        raise RuntimeError(
                            "No transcript tracks were found."
                        )

                break

            except Exception as exc:
                last_error = exc
                transcript = None

    if transcript is None:
        raise RuntimeError(
            "YouTube transcript retrieval failed. "
            f"Last error: {last_error}"
        )

    documents = []
    caption_group = []
    group_start = None
    group_end = 0.0
    group_chars = 0
    max_group_chars = 2500

    def save_group():
        if not caption_group:
            return

        documents.append(
            Document(
                page_content=" ".join(caption_group),
                metadata={
                    "source": video_title,
                    "source_type": "youtube",
                    "video_id": video_id,
                    "title": video_title,
                    "channel": video_metadata.get("channel"),
                    "thumbnail_url": video_metadata.get("thumbnail_url"),
                    "segment": len(documents) + 1,
                    "start": group_start,
                    "duration": max(0.0, group_end - group_start),
                    "timestamp": format_timestamp(group_start),
                    "timestamp_url": youtube_timestamp_url(
                        video_id, group_start
                    ),
                },
            )
        )

    for item in transcript:
        caption_text = str(
            getattr(item, "text", "")
        ).strip()

        if not caption_text:
            continue

        start = float(getattr(item, "start", 0))
        duration = float(getattr(item, "duration", 0))

        if (
            caption_group
            and group_chars + len(caption_text) + 1
            > max_group_chars
        ):
            save_group()
            caption_group = []
            group_start = None
            group_chars = 0

        if group_start is None:
            group_start = start

        caption_group.append(caption_text)
        group_chars += len(caption_text) + 1
        group_end = start + duration

    save_group()

    if documents:
        return documents, "available"

    raise RuntimeError("The transcript was empty.")


def load_pdf_documents(content: bytes, filename: str):
    if not content:
        raise RuntimeError("The uploaded PDF is empty.")

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = temporary_file.name

        documents = PyPDFLoader(temporary_path).load()

        for document in documents:
            document.metadata["source_type"] = "pdf"
            document.metadata["source"] = filename
            document.metadata["filename"] = filename

        if not documents or not any(
            document.page_content.strip() for document in documents
        ):
            raise RuntimeError(
                "No readable text was found in the PDF. "
                "If it contains scanned pages, OCR may be required."
            )

        return documents

    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)


def load_pdf(file_path: str):
    with open(file_path, "rb") as pdf_file:
        content = pdf_file.read()

    return load_pdf_documents(
        content,
        os.path.basename(file_path),
    )


def load_url_documents(url: str):
    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130 Safari/537.36"
            )
        },
    )
    response.raise_for_status()

    html = response.text

    html = re.sub(
        r"<(script|style|noscript)\b[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        raise RuntimeError(
            "No readable text was found on this webpage."
        )

    return [
        Document(
            page_content=text,
            metadata={
                "source": url,
                "source_type": "web",
                "url": url,
            },
        )
    ]


def load_webpage(url: str):
    return load_url_documents(url)

def load_transcript_documents(content: bytes, filename: str):
    """Convert an uploaded TXT or SRT transcript into RAG documents."""
    if not content:
        raise RuntimeError("The transcript file is empty.")

    try:
        transcript_text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            "The transcript must be a UTF-8 encoded TXT or SRT file."
        ) from exc

    if filename.lower().endswith(".srt"):
        transcript_text = re.sub(
            r"^\s*\d+\s*$",
            "",
            transcript_text,
            flags=re.MULTILINE,
        )
        transcript_text = re.sub(
            r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*"
            r"\d{2}:\d{2}:\d{2}[,.]\d{3}",
            "",
            transcript_text,
        )

    transcript_text = re.sub(
        r"<[^>]+>",
        " ",
        transcript_text,
    )
    transcript_text = re.sub(
        r"\s+",
        " ",
        transcript_text,
    ).strip()

    if not transcript_text:
        raise RuntimeError(
            "No readable transcript text was found in the file."
        )

    max_group_chars = 2500
    documents = []

    for start_index in range(0, len(transcript_text), max_group_chars):
        segment_text = transcript_text[start_index:start_index + max_group_chars]

        documents.append(
            Document(
                page_content=segment_text,
                metadata={
                    "source": filename,
                    "source_type": "youtube_transcript_upload",
                    "filename": filename,
                    "segment": len(documents) + 1,
                },
            )
        )

    return documents
# ============================================================
# ADVANCED DOCUMENT AND CODE LOADERS
# ============================================================

CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
    ".java", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".go", ".rs", ".php", ".rb",
    ".swift", ".kt", ".kts", ".sql",
    ".html", ".css", ".scss", ".json",
    ".xml", ".yaml", ".yml", ".sh",
    ".ps1", ".ipynb",
}


def _create_file_document(text: str, filename: str, source_type: str, **extra_metadata):
    """Create a LangChain Document with consistent file metadata."""

    cleaned_text = text.strip()

    if not cleaned_text:
        return []

    return [
        Document(
            page_content=cleaned_text,
            metadata={
                "source": filename,
                "filename": filename,
                "source_type": source_type,
                "file_extension": os.path.splitext(filename)[1].lower(),
                **extra_metadata,
            },
        )
    ]


def _decode_text_file(content: bytes, filename: str):
    """Decode common text-file encodings safely."""

    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise RuntimeError(
        f"Could not decode {filename}. Please upload a UTF-8 encoded file."
    )


def load_text_documents(content: bytes, filename: str):
    """Load plain text and Markdown files."""

    if not content:
        raise RuntimeError("The uploaded text file is empty.")

    text = _decode_text_file(content, filename)

    return _create_file_document(
        text,
        filename,
        "markdown" if filename.lower().endswith(".md") else "text",
    )


def load_docx_documents(content: bytes, filename: str):
    """Extract paragraphs and tables from Word documents."""

    if not content:
        raise RuntimeError("The Word document is empty.")

    from docx import Document as WordDocument

    try:
        word_file = WordDocument(io.BytesIO(content))

        sections = []

        for paragraph in word_file.paragraphs:
            text = paragraph.text.strip()

            if text:
                sections.append(text)

        for table_index, table in enumerate(word_file.tables, start=1):
            sections.append(f"\nTable {table_index}:")

            for row in table.rows:
                cells = [
                    cell.text.strip().replace("\n", " ")
                    for cell in row.cells
                ]

                if any(cells):
                    sections.append(" | ".join(cells))

        extracted_text = "\n\n".join(sections)

        documents = _create_file_document(
            extracted_text,
            filename,
            "docx",
        )

        if not documents:
            raise RuntimeError(
                "No readable paragraphs or tables were found in this Word document."
            )

        return documents

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Could not read the Word document: {exc}"
        ) from exc


def load_csv_documents(content: bytes, filename: str):
    """Convert CSV rows into readable structured text."""

    if not content:
        raise RuntimeError("The CSV file is empty.")

    text = _decode_text_file(content, filename)

    try:
        reader = csv.reader(io.StringIO(text))

        rows = list(reader)

        if not rows:
            raise RuntimeError("The CSV file contains no rows.")

        headers = rows[0]
        records = []

        for row_number, row in enumerate(rows[1:], start=1):
            values = []

            for index, value in enumerate(row):
                column = (
                    headers[index].strip()
                    if index < len(headers) and headers[index].strip()
                    else f"Column {index + 1}"
                )

                values.append(f"{column}: {value.strip()}")

            if values:
                records.append(
                    f"Record {row_number}:\n" + "\n".join(values)
                )

        extracted_text = (
            "CSV DATA\n"
            + "Columns: " + ", ".join(headers)
            + "\n\n"
            + "\n\n".join(records)
        )

        if not records:
            raise RuntimeError("The CSV file contains no data records.")

        return _create_file_document(
            extracted_text,
            filename,
            "csv",
            row_count=len(records),
            columns=", ".join(headers),
        )

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Could not read the CSV file: {exc}"
        ) from exc


def load_xlsx_documents(content: bytes, filename: str):
    """Extract rows from every worksheet in an Excel workbook."""

    if not content:
        raise RuntimeError("The Excel file is empty.")

    from openpyxl import load_workbook

    try:
        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=True,
        )

        documents = []

        for worksheet in workbook.worksheets:
            sections = [
                f"Worksheet: {worksheet.title}"
            ]

            rows = worksheet.iter_rows(values_only=True)

            headers = next(rows, None)

            if not headers:
                continue

            headers = [
                str(value).strip() if value is not None else f"Column {index + 1}"
                for index, value in enumerate(headers)
            ]

            sections.append("Columns: " + ", ".join(headers))

            record_count = 0

            for row_number, row in enumerate(rows, start=1):
                values = []

                for index, value in enumerate(row):
                    if value is None:
                        continue

                    column = (
                        headers[index]
                        if index < len(headers)
                        else f"Column {index + 1}"
                    )

                    values.append(f"{column}: {value}")

                if values:
                    record_count += 1
                    sections.append(
                        f"Record {row_number}:\n" + "\n".join(values)
                    )

            if record_count:
                documents.extend(
                    _create_file_document(
                        "\n\n".join(sections),
                        filename,
                        "xlsx",
                        sheet_name=worksheet.title,
                        row_count=record_count,
                        columns=", ".join(str(header) for header in headers),
                    )
                )

        workbook.close()

        if not documents:
            raise RuntimeError(
                "No readable data was found in the Excel worksheets."
            )

        return documents

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Could not read the Excel workbook: {exc}"
        ) from exc


def load_pptx_documents(content: bytes, filename: str):
    """Extract slide text and tables from PowerPoint presentations."""

    if not content:
        raise RuntimeError("The PowerPoint file is empty.")

    from pptx import Presentation

    try:
        presentation = Presentation(io.BytesIO(content))

        documents = []

        for slide_number, slide in enumerate(
            presentation.slides,
            start=1,
        ):
            sections = [
                f"Slide {slide_number}"
            ]

            for shape in slide.shapes:

                if shape.has_text_frame:
                    text = shape.text.strip()

                    if text:
                        sections.append(text)

                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [
                            cell.text.strip()
                            for cell in row.cells
                        ]

                        sections.append(" | ".join(cells))

            slide_text = "\n\n".join(sections)

            documents.extend(
                _create_file_document(
                    slide_text,
                    filename,
                    "pptx",
                    slide_number=slide_number,
                )
            )

        if not documents:
            raise RuntimeError(
                "No readable text was found in the PowerPoint presentation."
            )

        return documents

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Could not read the PowerPoint presentation: {exc}"
        ) from exc


def load_code_documents(content: bytes, filename: str):
    """Load source code and preserve filename and language metadata."""

    if not content:
        raise RuntimeError("The source-code file is empty.")

    extension = os.path.splitext(filename)[1].lower()

    if extension == ".ipynb":
        import json

        try:
            notebook = json.loads(content.decode("utf-8-sig"))

            code_sections = []

            for cell_number, cell in enumerate(
                notebook.get("cells", []),
                start=1,
            ):
                if cell.get("cell_type") == "code":
                    source = cell.get("source", "")

                    if isinstance(source, list):
                        source = "".join(source)

                    if source.strip():
                        code_sections.append(
                            f"# Notebook code cell {cell_number}\n{source}"
                        )

            extracted_text = "\n\n".join(code_sections)

        except Exception as exc:
            raise RuntimeError(
                f"Could not read the Jupyter notebook: {exc}"
            ) from exc

    else:
        extracted_text = _decode_text_file(content, filename)

    language = extension.lstrip(".") or "unknown"

    documents = _create_file_document(
        extracted_text,
        filename,
        "source_code",
        language=language,
    )

    if not documents:
        raise RuntimeError(
            "No readable source code was found in the file."
        )

    return documents


def load_file_documents(content: bytes, filename: str):
    """
    Automatically select the correct loader based on file extension.

    Returns a list of LangChain Documents ready for the RAG pipeline.
    """

    extension = os.path.splitext(filename)[1].lower()

    if extension == ".docx":
        return load_docx_documents(content, filename)

    if extension in {".txt", ".md"}:
        return load_text_documents(content, filename)

    if extension == ".csv":
        return load_csv_documents(content, filename)

    if extension == ".xlsx":
        return load_xlsx_documents(content, filename)

    if extension == ".pptx":
        return load_pptx_documents(content, filename)

    if extension in CODE_EXTENSIONS:
        return load_code_documents(content, filename)

    raise RuntimeError(
        f"Unsupported file format: {extension or 'unknown'}. "
        "Please upload a supported document, spreadsheet, presentation, "
        "text, or source-code file."
    )