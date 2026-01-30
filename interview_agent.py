import os
import json
import time
import uuid
import redis
import numpy as np
import httpx
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS = redis.from_url(REDIS_URL, decode_responses=True)

MISTRAL_KEY = os.getenv("Mistral_API_KEY")

# ---------------- LLM ----------------


async def call_llm(prompt):
    headers = {
        "Authorization": f"Bearer {MISTRAL_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "Hybrid-Interview-Agent",
    }

    payload = {
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "messages": [
            {
                "role": "system",
                "content": "You are a senior software engineer conducting a technical interview. Be strict but fair.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    return r.json()["choices"][0]["message"]["content"]


# ---------------- SESSION ----------------


def create_session(resume_id: str):
    session_id = str(uuid.uuid4())

    state = {
        "session_id": session_id,
        "resume_id": resume_id,
        "history": [],
        "score": 0,
        "started_at": time.time(),
    }

    REDIS.set(session_id, json.dumps(state), ex=3600)

    print("🧠 Session created:", session_id)
    return session_id, state


def get_state(session_id):
    raw = REDIS.get(session_id)
    return json.loads(raw) if raw else None


def save_state(session_id, state):
    REDIS.set(session_id, json.dumps(state), ex=3600)


# ---------------- AGENT ----------------


async def generate_first_question(resume_chunks, job_chunks=None):
    """
    If job_chunks is provided → targeted interview
    If not → general technical interview
    """
    # Join chunks into a single string for better prompting
    resume_text = (
        "\n".join(resume_chunks)
        if isinstance(resume_chunks, list)
        else str(resume_chunks)
    )
    job_text = (
        "\n".join(job_chunks)
        if job_chunks and isinstance(job_chunks, list)
        else str(job_chunks)
        if job_chunks
        else ""
    )

    mode = "targeted" if job_chunks else "general"

    if mode == "targeted":
        prompt = f"""
You are a professional technical interviewer.

Candidate Resume Highlights:
{resume_text}

Job Role Requirements:
{job_text}

Your task:
- Ask ONE clear, EASY technical question to start the interview
- The question must be directly relevant to the job role
- Do NOT include evaluation criteria
- Do NOT include follow-ups
- Just the question

Tone: Friendly, professional, realistic
"""
    else:
        prompt = f"""
You are a professional technical interviewer.

Candidate Resume Highlights:
{resume_text}

Your task:
- Ask ONE easy technical question based on the candidate's strongest skill
- Do NOT include evaluation criteria
- Do NOT include follow-ups
- Just the question

Tone: Friendly, professional, realistic
"""

    return await call_llm(prompt)


async def evaluate_answer(state, question, answer):
    """
    Evaluates candidate response and decides next step
    """

    prompt = f"""
You are an AI technical interviewer.

INTERVIEW STATE:
{json.dumps(state, indent=2)}

QUESTION:
{question}

CANDIDATE ANSWER:
{answer}

Your task:
1. Give SHORT feedback (2–3 lines max)
2. Rate the answer:
   - correctness (0–10)
   - clarity (0–10)
   - depth (0–10)
3. Decide difficulty for next question:
   - "easy"
   - "medium"
   - "hard"

Return JSON only in this format:

{{
  "feedback": "text",
  "scores": {{
    "correctness": 0,
    "clarity": 0,
    "depth": 0
  }},
  "next_difficulty": "easy|medium|hard"
}}
"""

    return await call_llm(prompt)


async def generate_followup_question(state, resume_chunks, job_chunks=None):
    """
    Adaptive interviewer — difficulty-based follow-up
    """

    difficulty = state.get("difficulty", "easy")
    mode = "targeted" if job_chunks else "general"

    if mode == "targeted":
        prompt = f"""
You are an adaptive technical interviewer.

Candidate Resume:
{resume_chunks}

Job Requirements:
{job_chunks}

Interview State:
{json.dumps(state, indent=2)}

Your task:
- Ask ONE {difficulty} level technical question
- It must relate to:
  - The previous answer OR
  - The job role
- No feedback
- No explanations
- Only the question
"""
    else:
        prompt = f"""
You are an adaptive technical interviewer.

Candidate Resume:
{resume_chunks}

Interview State:
{json.dumps(state, indent=2)}

Your task:
- Ask ONE {difficulty} level technical question
- It must relate to:
  - The previous answer OR
  - The candidate's strongest skills
- No feedback
- No explanations
- Only the question
"""

    return await call_llm(prompt)
