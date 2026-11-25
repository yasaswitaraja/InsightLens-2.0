import io
import os
import uuid
import traceback
from typing import Callable, TypeVar

from dotenv import load_dotenv
from pathlib import Path
from .auth import router as auth_router
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from .pipeline import InsightEngine
from .loaders import extract_video_id, CODE_EXTENSIONS
from fastapi import Depends
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from langchain_core.documents import Document

from .auth import get_current_user
from .database import get_db
from .models import User, Workspace, AnalysisSession

load_dotenv(Path(__file__).resolve().parent / ".env")


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="InsightLens API",
    version="3.0.2",
)
app.include_router(auth_router)
frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")

allow_origins = (
    ["*"]
    if frontend_origin.strip() == "*"
    else [
        origin.strip().rstrip("/")
        for origin in frontend_origin.split(",")
        if origin.strip()
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=frontend_origin.strip() != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# CONFIGURATION AND SESSION STORAGE
# ============================================================

ENGINES: dict[str, InsightEngine] = {}

try:
    MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
except ValueError:
    MAX_UPLOAD_MB = 20

if MAX_UPLOAD_MB < 1:
    MAX_UPLOAD_MB = 20
SUPPORTED_FILE_EXTENSIONS = (
    ".docx",
    ".txt",
    ".md",
    ".xlsx",
    ".pptx",
) + tuple(sorted(CODE_EXTENSIONS))
T = TypeVar("T")


# ============================================================
# REQUEST MODELS
# ============================================================

class AskRequest(BaseModel):
    session_id: str = Field(min_length=10)
    question: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="English", max_length=40)
    learning_mode: str = Field(default="explain", max_length=40)
    level: str = Field(default="Beginner", max_length=40)

class SessionRequest(BaseModel):
    session_id: str = Field(min_length=10)

class LanguageRequest(SessionRequest):
    language: str = Field(default="English", max_length=40)


class AddUrlsRequest(SessionRequest):
    urls: list[str] = Field(min_length=1, max_length=5)


# ============================================================
# COMMON HELPERS
# ============================================================

def get_engine(session_id: str) -> InsightEngine:
    engine = ENGINES.get(session_id)

    if engine is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Analysis session not found. "
                "Please analyze a source again."
            ),
        )

    return engine


def run_analysis(factory: Callable[[], T]) -> T:
    """Run an analysis operation and log its actual failure."""

    try:
        return factory()

    except HTTPException:
        raise

    except Exception as exc:
        print(
            f"[ANALYSIS ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                "Analysis failed. "
                "Check the backend logs for details."
            ),
        ) from exc


async def read_upload(
    file: UploadFile,
    allowed_extensions: tuple[str, ...],
    file_description: str,
) -> bytes:
    """Validate and read an uploaded file."""

    filename = file.filename or ""

    if not filename:
        raise HTTPException(
            status_code=400,
            detail=f"No {file_description} file was uploaded.",
        )

    if not filename.lower().endswith(allowed_extensions):
        allowed = ", ".join(allowed_extensions)

        raise HTTPException(
            status_code=400,
            detail=f"Supported file extensions: {allowed}.",
        )

    try:
        # Read at most one byte beyond the configured limit so
        # oversized files can be rejected without loading them fully.
        content = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)

    finally:
        await file.close()

    if not content:
        raise HTTPException(
            status_code=400,
            detail=f"The uploaded {file_description} file is empty.",
        )

    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=(
                f"The {file_description} file exceeds "
                f"the {MAX_UPLOAD_MB} MB upload limit."
            ),
        )

    return content



def get_or_create_default_workspace(
    db: Session,
    user_id: uuid.UUID,
) -> Workspace:
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.owner_id == user_id)
        .order_by(Workspace.created_at)
        .limit(1)
    )

    if workspace is None:
        workspace = Workspace(
            owner_id=user_id,
            name="My Workspace",
        )
        db.add(workspace)
        db.flush()

    return workspace


def get_owned_session(
    session_id: str,
    current_user: User,
    db: Session,
) -> AnalysisSession:
    record = db.scalar(
        select(AnalysisSession)
        .join(
            Workspace,
            AnalysisSession.workspace_id == Workspace.id,
        )
        .where(
            AnalysisSession.legacy_session_id == session_id,
            Workspace.owner_id == current_user.id,
        )
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis session not found.",
        )

    return record


def serialize_engine(engine: InsightEngine) -> dict:
    return jsonable_encoder({
        "sources": engine.sources,
        "chunks": [
            {
                "page_content": document.page_content,
                "metadata": document.metadata,
            }
            for document in engine.chunks
        ],
        "qa_history": engine.qa_history,
        "comparison": engine.comparison,
    })


def restore_engine(record: AnalysisSession) -> InsightEngine:
    state = record.engine_state_json or {}
    result = record.result_json or {}

    engine = InsightEngine()

    engine.sources = state.get("sources", [])

    engine.chunks = [
        Document(
            page_content=item["page_content"],
            metadata=item.get("metadata") or {},
        )
        for item in state.get("chunks", [])
    ]

    engine.summary = result.get("summary", "")
    engine.key_points = result.get("key_points", [])
    engine.findings = result.get("findings", [])
    engine.concepts = result.get("concepts", [])
    engine.charts = result.get("charts", [])

    engine.qa_history = state.get("qa_history", [])
    engine.comparison = state.get("comparison")

    if not engine.chunks:
        raise HTTPException(
            status_code=500,
            detail="Saved analysis has no searchable document content.",
        )

    engine._rebuild_vector_store(engine.chunks)

    return engine


def get_owned_engine(
    session_id: str,
    current_user: User,
    db: Session,
) -> InsightEngine:
    record = get_owned_session(
        session_id,
        current_user,
        db,
    )

    engine = ENGINES.get(session_id)

    if engine is None:
        engine = restore_engine(record)
        ENGINES[session_id] = engine

    return engine


def save_session(
    session_id: str,
    engine: InsightEngine,
    result: dict,
    current_user: User,
    db: Session,
    workspace_id: str | None = None,
) -> dict:
    """Save an analysis to the selected user's workspace."""
    if workspace_id:
        try:
            workspace_uuid = uuid.UUID(workspace_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid workspace ID.",
            )
        workspace = db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_uuid,
                Workspace.owner_id == current_user.id,
            )
        )
        if workspace is None:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found.",
            )
    else:
        workspace = get_or_create_default_workspace(
            db,
            current_user.id,
        )
    record = AnalysisSession(
        workspace_id=workspace.id,
        legacy_session_id=session_id,
        title=(engine.primary_source.get(
            "name",
            "Untitled analysis")
            if engine.primary_source else "Untitled analysis"
        ),
        result_json=jsonable_encoder(result),
        engine_state_json=serialize_engine(engine),
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    ENGINES[session_id] = engine
    result["session_id"] = session_id
    return result




def persist_existing_session(
    session_id: str,
    engine: InsightEngine,
    current_user: User,
    db: Session,
) -> None:
    """Update a previously saved analysis."""

    record = get_owned_session(
        session_id,
        current_user,
        db,
    )

    result = engine.result()

    record.result_json = jsonable_encoder(result)
    record.engine_state_json = serialize_engine(engine)

    if engine.primary_source:
        record.title = engine.primary_source.get(
            "name",
            "Untitled analysis",
        )

    db.commit()


# ============================================================
# ROOT AND HEALTH CHECK
# ============================================================

@app.get("/")
def root():
    return {
        "name": "InsightLens API",
        "status": "ok",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


# ============================================================
# YOUTUBE METADATA
# ============================================================

@app.get("/api/youtube")
def youtube_metadata(url: str):
    video_id = extract_video_id(url)

    if not video_id:
        raise HTTPException(
            status_code=400,
            detail="Invalid YouTube URL.",
        )

    return {
        "video_id": video_id,
        "watch_url": (
            f"https://www.youtube.com/watch?v={video_id}"
        ),
        "embed_url": (
            "https://www.youtube-nocookie.com/embed/"
            f"{video_id}"
        ),
        "thumbnail_url": (
            f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        ),
        "timestamp_url_example": (
            f"https://www.youtube.com/watch?v={video_id}&t=60"
        ),
    }


# ============================================================
# ANALYZE WEBPAGE URL
# ============================================================

@app.post("/api/analyze/url")
def analyze_url(
    url: str = Form(...),
    workspace_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cleaned_url = url.strip()

    if not cleaned_url:
        raise HTTPException(
            status_code=400,
            detail="URL cannot be empty.",
        )

    session_id = uuid.uuid4().hex

    print("[URL DEBUG] Creating InsightEngine", flush=True)

    engine = InsightEngine()

    print("[URL DEBUG] Starting URL analysis", flush=True)

    result = run_analysis(
        lambda: engine.analyze_url(cleaned_url)
    )

    print("[URL DEBUG] URL analysis completed", flush=True)

    return save_session(
        session_id,
        engine,
        result,
        current_user,
        db,
        workspace_id=workspace_id,
    )

# ============================================================
# ANALYZE PDF
# ============================================================

@app.post("/api/analyze/pdf")
async def analyze_pdf(
    file: UploadFile = File(...),
    workspace_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    print("[PDF DEBUG] Request received", flush=True)

    try:
        filename = file.filename or ""

        content = await read_upload(
            file,
            (".pdf",),
            "PDF",
        )

        print(
            f"[PDF DEBUG] File read: {len(content)} bytes",
            flush=True,
        )

        session_id = uuid.uuid4().hex
        engine = InsightEngine()

        print(
            "[PDF DEBUG] Starting PDF analysis",
            flush=True,
        )

        result = run_analysis(
            lambda: engine.analyze_pdf(content, filename)
        )

        print(
            "[PDF DEBUG] PDF analysis completed",
            flush=True,
        )

        response = save_session(
            session_id,
            engine,
            result,
            current_user,
            db,
            workspace_id=workspace_id,
        )

        print(
            "[PDF DEBUG] Returning response",
            flush=True,
        )

        return response

    except HTTPException:
        raise

    except Exception as exc:
        print(
            f"[PDF ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                "PDF analysis failed. "
                "Check the backend logs for details."
            ),
        ) from exc

    finally:
        await file.close()

# ============================================================
# ANALYZE GENERIC DOCUMENT, SPREADSHEET, PRESENTATION OR CODE
# ============================================================

@app.post("/api/analyze/file")
async def analyze_file(
    file: UploadFile = File(...),
    workspace_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        filename = file.filename or ""

        content = await read_upload(
            file,
            SUPPORTED_FILE_EXTENSIONS,
            "document",
        )

        session_id = uuid.uuid4().hex
        engine = InsightEngine()

        result = run_analysis(
            lambda: engine.add_file(content, filename)
        )

        response = save_session(
            session_id,
            engine,
            result,
            current_user,
            db,
            workspace_id=workspace_id,
        )

        return response

    except HTTPException:
        raise

    except Exception as exc:
        print(
            f"[FILE ANALYSIS ERROR] {type(exc).__name__}: {exc}",
            flush=True,
        )
        traceback.print_exc()

        raise HTTPException(
            status_code=500,
            detail=(
                "File analysis failed. "
                "Check the backend logs for details."
            ),
        ) from exc

    finally:
        await file.close()

# ============================================================
# ANALYZE UPLOADED TXT OR SRT TRANSCRIPT
# ============================================================

@app.post("/api/analyze/transcript")
async def analyze_transcript(
    file: UploadFile = File(...),
    workspace_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    filename = file.filename or ""

    try:
        content = await read_upload(
            file,
            (".txt", ".srt"),
            "transcript",
        )

        session_id = uuid.uuid4().hex
        engine = InsightEngine()

        result = run_analysis(
            lambda: engine.analyze_transcript(content, filename)
        )

        return save_session(
            session_id,
            engine,
            result,
            current_user,
            db,
            workspace_id=workspace_id,
        )

    finally:
        await file.close()

# ============================================================
# ADD WEBPAGE URLS TO AN EXISTING SESSION
# ============================================================

@app.post("/api/add/url")
def add_url(
    request: AddUrlsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    engine = get_owned_engine(
        request.session_id,
        current_user,
        db,
    )

    result = engine.result()

    for url in request.urls:
        cleaned_url = url.strip()

        if not cleaned_url:
            raise HTTPException(
                status_code=400,
                detail="An additional URL cannot be empty.",
            )

        result = run_analysis(
            lambda cleaned_url=cleaned_url: engine.add_url(
                cleaned_url
            )
        )

    result["session_id"] = request.session_id

    persist_existing_session(
        request.session_id,
        engine,
        current_user,
        db,
    )

    return result


# ============================================================
# ADD PDF TO AN EXISTING SESSION
# ============================================================

@app.post("/api/add/pdf")
async def add_pdf(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    workspace_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        engine = get_owned_engine(
            session_id,
            current_user,
            db,
        )

        filename = file.filename or ""

        content = await read_upload(
            file,
            (".pdf",),
            "PDF",
        )

        result = run_analysis(
            lambda: engine.add_pdf(content, filename)
        )

        result["session_id"] = session_id

        persist_existing_session(
            session_id,
            engine,
            current_user,
            db,
        )

        return result

    finally:
        await file.close()

# ============================================================
# ADD TXT OR SRT TRANSCRIPT TO AN EXISTING SESSION
# ============================================================

@app.post("/api/add/transcript")
async def add_transcript(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        engine = get_owned_engine(
            session_id,
            current_user,
            db,
        )

        filename = file.filename or ""

        content = await read_upload(
            file,
            (".txt", ".srt"),
            "transcript",
        )

        result = run_analysis(
            lambda: engine.add_transcript(content, filename)
        )

        result["session_id"] = session_id

        persist_existing_session(
            session_id,
            engine,
            current_user,
            db,
        )

        return result

    finally:
        await file.close()
# ============================================================
# ADD DOCUMENT, SPREADSHEET, PRESENTATION OR CODE FILE
# ============================================================

@app.post("/api/add/file")
async def add_file(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        engine = get_owned_engine(
            session_id,
            current_user,
            db,
        )

        filename = file.filename or ""

        content = await read_upload(
            file,
            SUPPORTED_FILE_EXTENSIONS,
            "document",
        )

        result = run_analysis(
            lambda: engine.add_file(content, filename)
        )

        result["session_id"] = session_id

        persist_existing_session(
            session_id,
            engine,
            current_user,
            db,
        )

        return result

    finally:
        await file.close()
# ============================================================
# COMPARE SOURCES IN AN EXISTING SESSION
# ============================================================

@app.post("/api/compare")
def compare(
    request: SessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    engine = get_owned_engine(
        request.session_id,
        current_user,
        db,
    )

    result = run_analysis(engine.compare)

    persist_existing_session(
        request.session_id,
        engine,
        current_user,
        db,
    )

    return result

# ============================================================
# ASK A QUESTION ABOUT THE ANALYZED SOURCE
# ============================================================


@app.post("/api/ask")
def ask(
    request: AskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    engine = get_owned_engine(
        request.session_id,
        current_user,
        db,
    )

    print(
        f"[ASK DEBUG] session_id={request.session_id}, "
        f"sources={[source.get('title') for source in engine.sources]}",
        f"chunk_count={len(engine.chunks)}",
        flush=True,
    )

    allowed_languages = {
        "English",
        "Telugu",
        "Hindi",
        "Tamil",
        "Kannada",
        "Malayalam",
        "Spanish",
        "French",
        "German",
    }

    language = request.language.strip()

    if language not in allowed_languages:
        raise HTTPException(
            status_code=400,
            detail="Please select a supported language.",
        )

    result = run_analysis(
        lambda: engine.ask(
            request.question.strip(),
            language=language,
        )
    )

    persist_existing_session(
        request.session_id,
        engine,
        current_user,
        db,
    )

    return result
# ============================================================
# ============================================================
# SOURCE-GROUNDED STUDY TOOLS
# ============================================================

class StudyToolRequest(SessionRequest):
    language: str = Field(default="English", max_length=40)


@app.post("/api/study/{tool}")
def generate_study_tool(
    tool: str,
    request: StudyToolRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if tool not in {"notes", "flashcards", "diagram"}:
        raise HTTPException(status_code=404, detail="Unknown study tool.")
    engine = get_owned_engine(request.session_id, current_user, db)
    allowed_languages = {"English", "Telugu", "Hindi", "Tamil", "Kannada",
        "Malayalam", "Spanish", "French", "German"}
    language = request.language.strip()
    if language not in allowed_languages:
        raise HTTPException(status_code=400, detail="Please select a supported language.")
    return run_analysis(lambda: engine.generate_study_tool(tool, language))


# TRANSLATE DISPLAYED ANALYSIS
# ============================================================

@app.post("/api/translate")
def translate(
    request: LanguageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    engine = get_owned_engine(
        request.session_id,
        current_user,
        db,
    )

    allowed_languages = {
        "English",
        "Telugu",
        "Hindi",
        "Tamil",
        "Kannada",
        "Malayalam",
        "Spanish",
        "French",
        "German",
    }

    language = request.language.strip()

    if language not in allowed_languages:
        raise HTTPException(
            status_code=400,
            detail="Please select a supported language.",
        )

    return run_analysis(
        lambda: engine.translate_result(language)
    )

# ============================================================
# EXPORT ANALYSIS AS A PDF REPORT
# ============================================================

@app.post("/api/export/pdf")
def export_pdf(
    session_id: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    engine = get_owned_engine(
        session_id,
        current_user,
        db,
    )

    pdf_bytes = run_analysis(engine.export_pdf)

    if (
        not isinstance(pdf_bytes, (bytes, bytearray))
        or not pdf_bytes
    ):
        raise HTTPException(
            status_code=500,
            detail=(
                "The report generator did not return "
                "valid PDF data."
            ),
        )

    return StreamingResponse(
        io.BytesIO(bytes(pdf_bytes)),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                'attachment; filename="InsightLens-Research-Report.pdf"'
            ),
        },
    )
# ============================================================
# LIST SAVED ANALYSIS HISTORY
# ============================================================

@app.get("/api/history")
def list_history(
    workspace_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(AnalysisSession)
        .join(
            Workspace,
            AnalysisSession.workspace_id == Workspace.id,
        )
        .where(
            Workspace.owner_id == current_user.id
        )
    )

    if workspace_id:
        try:
            workspace_uuid = uuid.UUID(workspace_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid workspace ID.",
            )

        workspace = db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_uuid,
                Workspace.owner_id == current_user.id,
            )
        )

        if workspace is None:
            raise HTTPException(
                status_code=404,
                detail="Workspace not found.",
            )

        query = query.where(
            AnalysisSession.workspace_id == workspace_uuid
        )

    records = db.scalars(
        query.order_by(AnalysisSession.updated_at.desc())
    ).all()

    return [
        {
            "id": str(record.id),
            "session_id": record.legacy_session_id,
            "workspace_id": str(record.workspace_id),
            "title": record.title,
            "source": (record.result_json or {}).get("source"),
            "created_at": (
                record.created_at.isoformat()
                if record.created_at else None
            ),
            "updated_at": (
                record.updated_at.isoformat()
                if record.updated_at else None
            ),
        }
        for record in records
    ]
# ============================================================
# GET A SAVED ANALYSIS
# ============================================================

@app.get("/api/history/{analysis_id}")
def get_history_detail(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid analysis ID.",
        )

    record = db.scalar(
        select(AnalysisSession)
        .join(
            Workspace,
            AnalysisSession.workspace_id == Workspace.id,
        )
        .where(
            AnalysisSession.id == analysis_uuid,
            Workspace.owner_id == current_user.id,
        )
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Saved analysis not found.",
        )

    state = record.engine_state_json or {}

    return {
        "id": str(record.id),
        "session_id": record.legacy_session_id,
        "workspace_id": str(record.workspace_id),
        "title": record.title,
        "result": record.result_json or {},
        "qa_history": state.get("qa_history", []),
        "comparison": state.get("comparison"),
        "created_at": (
            record.created_at.isoformat()
            if record.created_at else None
        ),
        "updated_at": (
            record.updated_at.isoformat()
            if record.updated_at else None
        ),
    }
class AnalysisUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
# ============================================================
# RENAME A SAVED ANALYSIS
# ============================================================

@app.patch("/api/history/{analysis_id}")
def rename_history_item(
    analysis_id: str,
    payload: AnalysisUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid analysis ID.",
        )

    record = db.scalar(
        select(AnalysisSession)
        .join(
            Workspace,
            AnalysisSession.workspace_id == Workspace.id,
        )
        .where(
            AnalysisSession.id == analysis_uuid,
            Workspace.owner_id == current_user.id,
        )
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Saved analysis not found.",
        )

    title = payload.title.strip()

    if not title:
        raise HTTPException(
            status_code=422,
            detail="Analysis title cannot be empty.",
        )

    record.title = title

    try:
        db.commit()
        db.refresh(record)
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Analysis renamed successfully.",
        "id": str(record.id),
        "title": record.title,
    }


# ============================================================
# DELETE A SAVED ANALYSIS
# ============================================================

@app.delete("/api/history/{analysis_id}")
def delete_history_item(
    analysis_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        analysis_uuid = uuid.UUID(analysis_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid analysis ID.",
        )

    record = db.scalar(
        select(AnalysisSession)
        .join(
            Workspace,
            AnalysisSession.workspace_id == Workspace.id,
        )
        .where(
            AnalysisSession.id == analysis_uuid,
            Workspace.owner_id == current_user.id,
        )
    )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Saved analysis not found.",
        )

    session_id = record.legacy_session_id
    record_id = str(record.id)

    try:
        db.delete(record)
        db.commit()
    except Exception:
        db.rollback()
        raise

    ENGINES.pop(session_id, None)

    return {
        "message": "Analysis deleted successfully.",
        "id": record_id,
    }

# ============================================================
# WORKSPACE MANAGEMENT
# ============================================================
class AnalysisUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=150)
    description: str | None = Field(default=None, max_length=1000)


def serialize_workspace(workspace: Workspace) -> dict:
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "description": workspace.description,
        "analysis_count": len(workspace.analysis_sessions),
        "created_at": (
            workspace.created_at.isoformat()
            if workspace.created_at else None
        ),
        "updated_at": (
            workspace.updated_at.isoformat()
            if workspace.updated_at else None
        ),
    }


@app.get("/api/workspaces")
def list_workspaces(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Ensure every user has at least one workspace.
    get_or_create_default_workspace(db, current_user.id)
    db.commit()

    workspaces = db.scalars(
        select(Workspace)
        .where(Workspace.owner_id == current_user.id)
        .order_by(Workspace.updated_at.desc())
    ).all()

    return [
        serialize_workspace(workspace)
        for workspace in workspaces
    ]


@app.post("/api/workspaces", status_code=201)
def create_workspace(
    payload: WorkspaceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = payload.name.strip()

    if not name:
        raise HTTPException(
            status_code=422,
            detail="Workspace name cannot be empty.",
        )

    workspace = Workspace(
        owner_id=current_user.id,
        name=name,
        description=(
            payload.description.strip()
            if payload.description and payload.description.strip()
            else None
        ),
    )

    db.add(workspace)
    db.commit()
    db.refresh(workspace)

    return serialize_workspace(workspace)


@app.patch("/api/workspaces/{workspace_id}")
def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        workspace_uuid = uuid.UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid workspace ID.",
        )

    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_uuid,
            Workspace.owner_id == current_user.id,
        )
    )

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail="Workspace not found.",
        )

    if "name" in payload.model_fields_set:
        if payload.name is None or not payload.name.strip():
            raise HTTPException(
                status_code=422,
                detail="Workspace name cannot be empty.",
            )
        workspace.name = payload.name.strip()

    if "description" in payload.model_fields_set:
        workspace.description = (
            payload.description.strip()
            if payload.description and payload.description.strip()
            else None
        )

    db.commit()
    db.refresh(workspace)

    return serialize_workspace(workspace)


@app.delete("/api/workspaces/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        workspace_uuid = uuid.UUID(workspace_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid workspace ID.",
        )

    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_uuid,
            Workspace.owner_id == current_user.id,
        )
    )

    if workspace is None:
        raise HTTPException(
            status_code=404,
            detail="Workspace not found.",
        )

    # Keep at least one workspace for each user.
    fallback = db.scalar(
        select(Workspace)
        .where(
            Workspace.owner_id == current_user.id,
            Workspace.id != workspace.id,
        )
        .order_by(Workspace.created_at)
        .limit(1)
    )

    if fallback is None:
        raise HTTPException(
            status_code=400,
            detail="You must keep at least one workspace.",
        )

    # Preserve saved analyses by moving them before deletion.
    analyses = db.scalars(
        select(AnalysisSession).where(
            AnalysisSession.workspace_id == workspace.id
        )
    ).all()
    deleted_session_ids = [
    analysis.legacy_session_id
    for analysis in analyses
]
    for analysis in analyses:
        db.delete(analysis)
    db.delete(workspace)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    for session_id in deleted_session_ids:
        ENGINES.pop(session_id, None)
    return{
        "message" : "Workspace deleted. Its saved analyses were preserved.",
        "fallback_workspace_id" : str(fallback.id),
        "deleted_analysis_count" : len(deleted_session_ids),
    }

    for analysis in analyses:
        analysis.workspace = fallback

    db.delete(workspace)
    db.commit()

    return {
        "message": "Workspace deleted. Its saved analyses were preserved.",
        "moved_to_workspace_id": str(fallback.id),
    }
