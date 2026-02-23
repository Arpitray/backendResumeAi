from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
import os
import uuid
import json
import redis
import asyncio
from reader import read_pdf, chunk_text
from memory import (
    get_job_chunks,
    get_resume_chunks,
    store_chunks,
    search_resume,
    search_job,
    generate_ai_feedback,
    generate_learning_path,
    get_collection,
    clean_llm_text,
)
from core_match import run_match
from fastapi.middleware.cors import CORSMiddleware
from llm import call_answer_llm
from pydantic import BaseModel
from typing import Optional
from interview_agent import (
    create_session,
    get_state,
    save_state,
    generate_first_question,
    evaluate_answer,
    generate_followup_question,
)
from dotenv import load_dotenv
from speech import router as speech_router
from sqlalchemy.ext.asyncio import AsyncSession

# ── Auth imports ──────────────────────────────────────────────────────────────
from database import engine, get_db
from models import Base, User, UserResume
from auth import get_current_user, verify_ownership
from routes.auth_routes import router as auth_router
from routes.oauth_routes import router as oauth_router

load_dotenv()

app = FastAPI(
    title="AI Resume Backend",
    description="AI-powered resume analysis, job matching, interview agent, and career coaching.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://solitaires.arpitray.me",
        "https://frontend-resume-brown.vercel.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(speech_router)
app.include_router(auth_router)     # /auth/register, /auth/login, /auth/refresh, /auth/me, /auth/logout
app.include_router(oauth_router)    # /auth/oauth/google, /auth/oauth/github

# ── DB startup — create tables if they don't exist ───────────────────────────

@app.on_event("startup")
async def startup():
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables ready")
    except Exception as e:
        print(f"⚠️  Database not available: {e}")
        print("⚠️  Auth endpoints will fail until PostgreSQL is running.")
        print("⚠️  All other endpoints (resume, job, match, interview) still work.")

# ── Redis (optional, for caching + interview sessions) ────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Make Redis optional for local development
try:
    redis_client = redis.from_url(
        REDIS_URL, decode_responses=True, socket_connect_timeout=2
    )
    redis_client.ping()
    print("✅ Redis connected")
except Exception as e:
    print(f"⚠️ Redis not available: {e}")
    print("⚠️ Running without cache (slower, but functional)")
    redis_client = None


# ── Pydantic request schemas ──────────────────────────────────────────────────

class QuestionRequest(BaseModel):
    resume_id: str
    question: str


class JobRequest(BaseModel):
    description: str


class StartInterviewRequest(BaseModel):
    resume_id: str
    job_id: Optional[str] = None


class InterviewAnswer(BaseModel):
    session_id: str
    question: str
    answer: str


# ── Public endpoints ──────────────────────────────────────────────────────────

@app.get("/", tags=["health"])
def health():
    return {"status": "AI Career Agent running"}


@app.get("/info", tags=["health"])
def info():
    return {"version": "1.0.0", "description": "AI Resume Backend"}


# ── Resume endpoints (protected) ──────────────────────────────────────────────

@app.post("/upload-resume", tags=["resume"])
async def upload_resume(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a PDF resume, parse + embed it, and register ownership."""
    print(f"📥 /upload-resume HIT — user: {current_user.email}")

    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)

    resume_id = f"{uuid.uuid4()}.pdf"
    path = os.path.join(upload_dir, resume_id)

    with open(path, "wb") as f:
        f.write(await file.read())

    print("📄 PDF saved:", path)

    # ── Extract text with multi-library fallback ─────────────────────────────
    try:
        text = read_pdf(path)
    except ValueError as exc:
        # Unreadable PDF — tell the client clearly instead of silently storing 0 chunks
        os.remove(path)
        raise HTTPException(
            status_code=422,
            detail=(
                f"Could not extract text from your PDF. "
                f"Please make sure it is not a scanned/image-only or encrypted file. "
                f"({exc})"
            ),
        )

    chunks = chunk_text(text)
    print("📦 Chunks:", len(chunks))

    if len(chunks) == 0:
        os.remove(path)
        raise HTTPException(
            status_code=422,
            detail=(
                "Your PDF was saved but no readable text was found. "
                "Please upload a text-based PDF (not a scanned image)."
            ),
        )

    store_chunks(chunks, resume_id)

    # Register ownership so all other endpoints can verify it
    db.add(UserResume(
        user_id=current_user.id,
        resume_id=resume_id,
        filename=file.filename or "resume.pdf",
    ))
    await db.flush()

    return {"status": "uploaded", "stored_as": resume_id, "chunks": len(chunks)}


@app.get("/auth/resumes", tags=["resume"])
async def list_my_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all resumes belonging to the current authenticated user."""
    from sqlalchemy import select as sa_select
    result = await db.execute(
        sa_select(UserResume).where(UserResume.user_id == current_user.id)
    )
    resumes = result.scalars().all()
    return [
        {
            "resume_id": r.resume_id,
            "filename": r.filename,
            "uploaded_at": r.uploaded_at.isoformat(),
        }
        for r in resumes
    ]


@app.post("/process-resume/{file_id}", tags=["resume"])
async def process_resume(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_ownership(str(current_user.id), file_id, db)
    path = os.path.join("uploads", file_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Resume not found")
    text = read_pdf(path)
    chunks = chunk_text(text)
    previews = [{"id": i, "preview": chunk[:150]} for i, chunk in enumerate(chunks[:5])]
    return {"status": "processed", "total_chunks": len(chunks), "previews": previews}


@app.post("/index-resume/{file_id}", tags=["resume"])
async def index_resume(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_ownership(str(current_user.id), file_id, db)
    path = os.path.join("uploads", file_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Resume not found")
    text = read_pdf(path)
    chunks = chunk_text(text)
    store_chunks(chunks, file_id)
    return {"status": "indexed", "resume_id": file_id, "chunks": len(chunks)}


# ── Job endpoint (protected) ──────────────────────────────────────────────────

@app.post("/upload-job", tags=["job"])
async def upload_job(
    req: JobRequest,
    current_user: User = Depends(get_current_user),
):
    job_id = str(uuid.uuid4())
    chunks = chunk_text(req.description)
    store_chunks(chunks=chunks, doc_id=job_id, doc_type="job")
    return {"status": "job stored", "job_id": job_id, "chunks": len(chunks)}


# ── Match endpoints (protected) ───────────────────────────────────────────────

@app.post("/match", tags=["match"])
async def match_resume(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")
    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    await verify_ownership(str(current_user.id), resume_id, db)

    cache_key = f"match:{resume_id}:{job_id}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                print("⚡ Cache HIT for Match")
                return json.loads(cached)
        except Exception as e:
            print(f"⚠️ Redis read failed: {e}")

    result, error = run_match(resume_id, job_id)
    if error:
        raise HTTPException(status_code=404, detail=error)

    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")
    return result


@app.post("/match/coach", tags=["match"])
async def ai_coach(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")
    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    await verify_ownership(str(current_user.id), resume_id, db)

    cache_key = f"coach:{resume_id}:{job_id}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                print("⚡ Cache HIT for AI Coach")
                return json.loads(cached)
        except Exception as e:
            print(f"⚠️ Redis read failed: {e}")

    match_data, error = run_match(resume_id, job_id)
    if error:
        raise HTTPException(status_code=404, detail=error)

    print("🤖 Running AI Resume Coach...")
    ai_feedback = await generate_ai_feedback(
        resume_chunks=[m["resume_chunk"] for m in match_data["top_matches"]],
        job_chunks=[m["job_match"] for m in match_data["top_matches"]],
    )
    result = {"ai_feedback": ai_feedback}

    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")
    return result


@app.post("/match/roadmap", tags=["match"])
async def learning_roadmap(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")
    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    await verify_ownership(str(current_user.id), resume_id, db)

    cache_key = f"roadmap:{resume_id}:{job_id}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                print("⚡ Cache HIT for Roadmap")
                return json.loads(cached)
        except Exception as e:
            print(f"⚠️ Redis read failed: {e}")

    col = get_collection()
    resume_data = col.get(
        where={"$and": [{"doc_id": resume_id}, {"type": "resume"}]},
        include=["documents"],
    )
    job_data = col.get(
        where={"$and": [{"doc_id": job_id}, {"type": "job"}]},
        include=["documents"],
    )
    if not resume_data["documents"] or not job_data["documents"]:
        raise HTTPException(status_code=404, detail="Resume or Job not found")

    print("📚 Generating Skill Gap Roadmap...")
    learning_agent_result = await generate_learning_path(
        resume_text="\n".join(resume_data["documents"][:3]),
        job_text="\n".join(job_data["documents"][:2]),
    )

    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(learning_agent_result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")
    return learning_agent_result


@app.post("/match/full-analysis", tags=["match"])
async def full_analysis(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Runs AI Coach + Roadmap in parallel (40–60% faster than calling separately)."""
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")
    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    await verify_ownership(str(current_user.id), resume_id, db)

    cache_key = f"full:{resume_id}:{job_id}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                print("⚡ Cache HIT for Full Analysis")
                return json.loads(cached)
        except Exception as e:
            print(f"⚠️ Redis read failed: {e}")

    match_data, error = run_match(resume_id, job_id)
    if error:
        raise HTTPException(status_code=404, detail=error)

    col = get_collection()
    resume_data = col.get(
        where={"$and": [{"doc_id": resume_id}, {"type": "resume"}]},
        include=["documents"],
    )
    job_data = col.get(
        where={"$and": [{"doc_id": job_id}, {"type": "job"}]},
        include=["documents"],
    )

    print("🚀 Running AI Coach + Roadmap in parallel...")
    ai_feedback, learning_agent_result = await asyncio.gather(
        generate_ai_feedback(
            resume_chunks=[m["resume_chunk"] for m in match_data["top_matches"]],
            job_chunks=[m["job_match"] for m in match_data["top_matches"]],
        ),
        generate_learning_path(
            resume_text="\n".join(resume_data["documents"][:3]),
            job_text="\n".join(job_data["documents"][:2]),
        ),
    )

    result = {
        "match_score": match_data["match_score_percent"],
        "top_matches": match_data["top_matches"],
        "ai_feedback": ai_feedback,
        "learning_path": learning_agent_result,
    }

    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")
    return result


# ── Interview endpoints (protected) ───────────────────────────────────────────

@app.post("/interview/start", tags=["interview"])
async def start_interview(
    req: StartInterviewRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    print(f"📥 /interview/start — user: {current_user.email}, resume: {req.resume_id}")
    await verify_ownership(str(current_user.id), req.resume_id, db)

    session_id, state = create_session(req.resume_id)
    state["user_id"] = str(current_user.id)

    if req.job_id:
        job_chunks = get_job_chunks(req.job_id)
        resume_chunks = get_resume_chunks(req.resume_id)
        first_question = await generate_first_question(
            resume_chunks=resume_chunks, job_chunks=job_chunks
        )
        state["mode"] = "targeted"
    else:
        resume_chunks = get_resume_chunks(req.resume_id)
        first_question = await generate_first_question(resume_chunks=resume_chunks)
        state["mode"] = "general"

    state["current_question"] = first_question
    save_state(session_id, state)
    return {"session_id": session_id, "question": first_question, "mode": state["mode"]}


@app.post("/interview/answer", tags=["interview"])
async def submit_answer(
    req: InterviewAnswer,
    current_user: User = Depends(get_current_user),
):
    print("📥 Interview answer received")

    state = get_state(req.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    if state.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=403, detail="Session does not belong to you")

    evaluation = await evaluate_answer(
        state=state, question=state["current_question"], answer=req.answer
    )

    try:
        cleaned = evaluation.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        eval_data = json.loads(cleaned.strip())
    except Exception:
        print("⚠️ Failed to parse AI evaluation JSON:", evaluation)
        raise HTTPException(status_code=500, detail="AI evaluation failed")

    state["history"].append({
        "question": state["current_question"],
        "answer": req.answer,
        "feedback": eval_data["feedback"],
        "scores": eval_data["scores"],
    })
    state["difficulty"] = eval_data["next_difficulty"]

    resume_chunks = search_resume(
        query="skills experience projects", resume_id=state["resume_id"]
    )
    job_chunks = None
    if state.get("job_id"):
        job_chunks = search_job(query="job requirements", job_id=state["job_id"])

    next_question = await generate_followup_question(
        state=state, resume_chunks=resume_chunks, job_chunks=job_chunks
    )
    state["current_question"] = next_question
    save_state(req.session_id, state)

    return {
        "feedback": eval_data["feedback"],
        "scores": eval_data["scores"],
        "next_question": next_question,
        "difficulty": state["difficulty"],
    }


@app.get("/interview/report/{session_id}", tags=["interview"])
async def interview_report(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    state = get_state(session_id)
    if not state:
        return {"error": "Session expired"}

    if state.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=403, detail="Session does not belong to you")

    return {
        "final_score": state.get("score", 0),
        "strengths": state.get("strengths", []),
        "weaknesses": state.get("weaknesses", []),
        "summary": "Candidate shows strong full-stack fundamentals and system thinking.",
    }


# ── Q&A endpoint (protected) ──────────────────────────────────────────────────

@app.post("/ask", tags=["qa"])
async def ask(
    req: QuestionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await verify_ownership(str(current_user.id), req.resume_id, db)
    print("🧠 Received question:", req.question)

    chunks = search_resume(req.question, req.resume_id)
    context = ""
    citations = []

    if not chunks:
        return {
            "query": req.question,
            "result": (
                "I couldn't find any content from your resume. "
                "This usually means the resume wasn't indexed yet or the file contained no extractable text. "
                "Please re-upload your resume and ensure it is a text-based PDF (not a scanned image)."
            ),
            "citations": [],
        }

    for c in chunks:
        context += f"[Chunk {c['chunk_id']}]\n{c['text']}\n\n"
        citations.append(f"Chunk {c['chunk_id']}: {c['preview']}")

    prompt = f"""
You are an AI career assistant.

Answer the question using ONLY the context below.
Cite sources using chunk numbers.

Context:
{context}

Question:
{req.question}
"""

    answer = await call_answer_llm(prompt)
    return {"query": req.question, "result": clean_llm_text(answer), "citations": citations}

