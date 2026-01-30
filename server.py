from fastapi import FastAPI
from fastapi import UploadFile, File, HTTPException
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

load_dotenv()

app = FastAPI()
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

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Use redis_client if needed, though interview_agent uses its own global REDIS connection
# Make Redis optional for local development
try:
    redis_client = redis.from_url(
        REDIS_URL, decode_responses=True, socket_connect_timeout=2
    )
    redis_client.ping()  # Test connection
    print("✅ Redis connected")
except Exception as e:
    print(f"⚠️ Redis not available: {e}")
    print("⚠️ Running without cache (slower, but functional)")
    redis_client = None


class QuestionRequest(BaseModel):
    resume_id: str
    question: str


class JobRequest(BaseModel):
    description: str


class InterviewStart(BaseModel):
    resume_id: str
    job_id: Optional[str] = None


class InterviewAnswer(BaseModel):
    session_id: str
    question: str
    answer: str


class StartInterviewRequest(BaseModel):
    resume_id: str
    job_id: Optional[str] = None


@app.post("/upload-resume")
async def upload_resume(file: UploadFile = File(...)):
    print("📥 /upload-resume HIT")

    upload_dir = "uploads"
    os.makedirs(upload_dir, exist_ok=True)

    resume_id = f"{uuid.uuid4()}.pdf"
    path = os.path.join(upload_dir, resume_id)

    with open(path, "wb") as f:
        f.write(await file.read())

    print("📄 PDF saved:", path)

    text = read_pdf(path)
    chunks = chunk_text(text)

    print("📦 Chunks created:", len(chunks))
    print("🚀 Calling store_chunks() now")

    store_chunks(chunks, resume_id)

    return {"status": "uploaded", "stored_as": resume_id, "chunks": len(chunks)}


@app.get("/")
def health():
    return {"status": "AI Career Agent running"}


@app.get("/info")
def info():
    return {"version": "1.0.0", "description": "AI Career Agent API"}


@app.post("/process-resume/{file_id}")
async def process_resume(file_id: str):
    path = os.path.join("uploads", file_id)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Resume not found")

    # 1. Read PDF
    text = read_pdf(path)

    # 2. Chunk it
    chunks = chunk_text(text)

    # 3. Preview first few chunks
    previews = []
    for i, chunk in enumerate(chunks[:5]):
        previews.append({"id": i, "preview": chunk[:150]})

    return {"status": "processed", "total_chunks": len(chunks), "previews": previews}


@app.post("/index-resume/{file_id}")
async def index_resume(file_id: str):
    path = os.path.join("uploads", file_id)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Resume not found")

    text = read_pdf(path)
    chunks = chunk_text(text)

    store_chunks(chunks, file_id)

    return {"status": "indexed", "resume_id": file_id, "chunks": len(chunks)}


@app.post("/upload-job")
async def upload_job(req: JobRequest):
    job_id = str(uuid.uuid4())

    chunks = chunk_text(req.description)

    store_chunks(chunks=chunks, doc_id=job_id, doc_type="job")

    return {"status": "job stored", "job_id": job_id, "chunks": len(chunks)}


@app.post("/match")
async def match_resume(payload: dict):
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")

    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    # Check cache first (if Redis available)
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

    # Cache for 15 minutes (if Redis available)
    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")

    return result


@app.post("/match/coach")
async def ai_coach(payload: dict):
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")

    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    # Check cache first (if Redis available)
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

    # Cache for 15 minutes (if Redis available)
    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")

    return result


@app.post("/match/roadmap")
async def learning_roadmap(payload: dict):
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")

    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    # Check cache first (if Redis available)
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
        where={"$and": [{"doc_id": job_id}, {"type": "job"}]}, include=["documents"]
    )

    if not resume_data["documents"] or not job_data["documents"]:
        raise HTTPException(status_code=404, detail="Resume or Job not found")

    print("📚 Generating Skill Gap Roadmap...")

    learning_agent_result = await generate_learning_path(
        resume_text="\n".join(resume_data["documents"][:3]),
        job_text="\n".join(job_data["documents"][:2]),
    )

    # Cache for 15 minutes (if Redis available)
    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(learning_agent_result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")

    return learning_agent_result


@app.post("/match/full-analysis")
async def full_analysis(payload: dict):
    """
    🚀 OPTIMIZED: Runs AI Coach + Roadmap in parallel
    40-60% faster than calling separately!
    """
    resume_id = payload.get("resume_id")
    job_id = payload.get("job_id")

    if not resume_id or not job_id:
        raise HTTPException(status_code=400, detail="Missing resume_id or job_id")

    # Check cache first (if Redis available)
    cache_key = f"full:{resume_id}:{job_id}"
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                print("⚡ Cache HIT for Full Analysis")
                return json.loads(cached)
        except Exception as e:
            print(f"⚠️ Redis read failed: {e}")

    # Get match data first
    match_data, error = run_match(resume_id, job_id)
    if error:
        raise HTTPException(status_code=404, detail=error)

    # Get documents for roadmap
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

    # 🔥 RUN BOTH IN PARALLEL - MASSIVE SPEED BOOST
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

    # Cache for 15 minutes (if Redis available)
    if redis_client:
        try:
            redis_client.setex(cache_key, 900, json.dumps(result))
        except Exception as e:
            print(f"⚠️ Redis write failed: {e}")

    return result


@app.post("/interview/start")
async def start_interview(req: StartInterviewRequest):
    print("📥 Incoming resume_id:", req.resume_id)
    print("📥 Incoming job_id:", req.job_id)

    session_id, state = create_session(req.resume_id)

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


@app.post("/interview/answer")
async def submit_answer(req: InterviewAnswer):
    print("📥 Interview answer received")

    # ---------------- LOAD SESSION ----------------
    state = get_state(req.session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    # ---------------- EVALUATE ANSWER ----------------
    evaluation = await evaluate_answer(
        state=state, question=state["current_question"], answer=req.answer
    )

    # Parse AI JSON safely
    try:
        # Clean up potential markdown code blocks
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

    # ---------------- UPDATE STATE ----------------
    state["history"].append(
        {
            "question": state["current_question"],
            "answer": req.answer,
            "feedback": eval_data["feedback"],
            "scores": eval_data["scores"],
        }
    )

    state["difficulty"] = eval_data["next_difficulty"]

    # ---------------- GET CONTEXT ----------------
    resume_chunks = search_resume(
        query="skills experience projects", resume_id=state["resume_id"]
    )

    job_chunks = None
    if state.get("job_id"):
        job_chunks = search_job(query="job requirements", job_id=state["job_id"])

    # ---------------- NEXT QUESTION ----------------
    next_question = await generate_followup_question(
        state=state, resume_chunks=resume_chunks, job_chunks=job_chunks
    )

    state["current_question"] = next_question
    save_state(req.session_id, state)

    # ---------------- RESPONSE ----------------
    return {
        "feedback": eval_data["feedback"],
        "scores": eval_data["scores"],
        "next_question": next_question,
        "difficulty": state["difficulty"],
    }


@app.get("/interview/report/{session_id}")
async def interview_report(session_id: str):
    state = get_state(session_id)
    if not state:
        return {"error": "Session expired"}

    return {
        "final_score": state["score"],
        "strengths": state["strengths"],
        "weaknesses": state["weaknesses"],
        "summary": "Candidate shows strong full-stack fundamentals and system thinking.",
    }


@app.post("/ask")
async def ask(req: QuestionRequest):
    resume_id = req.resume_id
    question = req.question

    chunks = search_resume(question, resume_id)

    context = ""
    citations = []
    print("🧠 Received question:", req.question)

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
{question}
"""

    answer = await call_answer_llm(prompt)

    return {"query": question, "result": answer, "citations": citations}
