from fastapi import FastAPI
from fastapi import UploadFile, File, HTTPException
import os
import uuid
import json
import redis
from reader import read_pdf, chunk_text
from memory import (
    get_job_chunks,
    get_resume_chunks,
    match_resume_to_job,
    store_chunks,
    search_resume,
    search_job,
)

from fastapi.middleware.cors import CORSMiddleware
from llm import call_answer_llm
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

from interview_agent import (
    create_session,
    get_state,
    save_state,
    generate_first_question,
    evaluate_answer,
    generate_followup_question,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Use redis_client if needed, though interview_agent uses its own global REDIS connection
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

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


@app.get("/match/{resume_id}/{job_id}")
async def match(resume_id: str, job_id: str):
    return await match_resume_to_job(resume_id, job_id)


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
    except:
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
