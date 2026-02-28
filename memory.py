import os
import chromadb
from chromadb.config import Settings
import re
import numpy as np

# Suppress ChromaDB telemetry before any chromadb usage
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

from llm import call_answer_llm

# Load embedding model once
_model = None

# Global chroma client for persistence
_chroma = None
_collection = None


def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


async def generate_ai_feedback(resume_chunks, job_chunks):
    resume_text = "\n".join(resume_chunks[:5])
    job_text = "\n".join(job_chunks[:3])

    prompt = f"""
You are a professional AI resume coach helping candidates optimize their resumes for ATS systems.

JOB REQUIREMENTS:
{job_text}

CURRENT RESUME CONTENT:
{resume_text}

Provide actionable, conversational feedback in the following areas:

1. **Missing Skills**: Identify 3-5 key skills or technologies from the job description that are missing or underrepresented in the resume.

2. **Resume Improvements**: Suggest 2-3 specific ways to rewrite existing resume bullets to better align with the job requirements. For each suggestion:
   - Show the original text
   - Provide an improved version that incorporates relevant keywords
   - Explain in a friendly, conversational tone why this change will help pass ATS screening

Return your response as valid JSON in this format:
{{
  "missing_skills": ["skill1", "skill2", "skill3"],
  "suggestions": [
    {{
      "before": "original bullet point",
      "after": "improved bullet point with keywords",
      "reason": "Conversational explanation of why this helps"
    }}
  ]
}}

Make your tone helpful and encouraging, not robotic.
"""

    reply = await call_answer_llm(prompt)

    # Parse JSON response
    import json
    import re  # Added re import for better cleanup

    try:
        # Clean up potential markdown code blocks
        cleaned = reply.strip()

        # More robust cleaning
        # Remove everything before the first `{`
        first_brace = cleaned.find("{")
        if first_brace != -1:
            cleaned = cleaned[first_brace:]

        # Remove everything after the last `}`
        last_brace = cleaned.rfind("}")
        if last_brace != -1:
            cleaned = cleaned[: last_brace + 1]

        return json.loads(cleaned)
    except json.JSONDecodeError:
        print("⚠️ Failed to parse AI feedback JSON:", reply)
        return {
            "missing_skills": [],
            "suggestions": [],
            "error": "Failed to parse AI response",
        }


async def generate_learning_path(resume_text, job_text):
    prompt = f"""
You are a friendly AI Career Mentor helping someone prepare for their dream job.

THE JOB THEY WANT:
{job_text}

THEIR CURRENT BACKGROUND:
{resume_text}

Create a personalized learning roadmap:

1. **Skill Gaps**: List 3-5 specific skills they need to develop, ranked by importance for getting hired

2. **Learning Roadmap**: Design a realistic 7-14 day study plan. For each day:
   - Set a clear, achievable goal
   - List specific topics to learn (be concrete, not vague)
   - Include a small hands-on task they can complete that day

3. **Portfolio Project**: Suggest ONE impressive project they can build to showcase these new skills. Make it specific and relevant to the job.

Return as valid JSON:
{{
  "skill_gaps": ["skill1", "skill2", "skill3"],
  "roadmap": [
    {{
      "day": 1,
      "goal": "Specific daily goal",
      "what_to_learn": ["Topic 1", "Topic 2"],
      "mini_task": "Concrete task to complete"
    }}
  ],
  "portfolio_project": "Detailed project description"
}}

Be encouraging and specific. Avoid generic advice.
"""

    reply = await call_answer_llm(prompt)

    # Parse JSON response
    import json
    import re

    try:
        # Clean up potential markdown code blocks
        cleaned = reply.strip()

        # More robust cleaning
        # Remove everything before the first `{`
        first_brace = cleaned.find("{")
        if first_brace != -1:
            cleaned = cleaned[first_brace:]

        # Remove everything after the last `}`
        last_brace = cleaned.rfind("}")
        if last_brace != -1:
            cleaned = cleaned[: last_brace + 1]

        return json.loads(cleaned)
    except json.JSONDecodeError:
        print("⚠️ Failed to parse learning path JSON:", reply)
        return {
            "skill_gaps": [],
            "roadmap": [],
            "portfolio_project": "",
            "error": "Failed to parse AI response",
        }


def get_job_chunks(job_id, k=5):
    col = get_collection()

    results = col.get(
        where={"$and": [{"doc_id": job_id}, {"type": "job"}]}, include=["documents"]
    )

    return results["documents"] or []


def get_resume_chunks(resume_id):
    col = get_collection()

    results = col.get(
        where={"$and": [{"doc_id": resume_id}, {"type": "resume"}]},
        include=["documents"],
    )

    return results["documents"] or []


def clean_llm_text(text: str) -> str:
    if not text:
        return ""

    # Remove markdown bold/italic
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)

    # Remove chunk citations like [Chunk 1], (Chunk 2)
    text = re.sub(r"\[?Chunk\s*\d+\]?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(Chunk\s*\d+\)", "", text, flags=re.IGNORECASE)

    # Remove extra newlines
    text = text.replace("\\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Clean bullet formatting
    text = text.replace("•", "-")

    return text.strip()


def get_collection():
    global _chroma, _collection

    if _collection is not None:
        return _collection

    # Ensure vector_store directory exists before initializing Chroma
    os.makedirs("./vector_store", exist_ok=True)

    # Compatible with both ChromaDB 0.3.x (old) and 0.4.x (new)
    try:
        # Try 0.4.x syntax
        _chroma = chromadb.PersistentClient(
            path="./vector_store", 
            settings=Settings(anonymized_telemetry=False, is_persistent=True)
        )
    except Exception as e:
        print(f"⚠️ ChromaDB initialization warning: {e}")
        # Fallback to 0.3.x syntax or a more basic client
        try:
            _chroma = chromadb.Client(
                Settings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory="./vector_store",
                    anonymized_telemetry=False,
                )
            )
        except Exception as e2:
            print(f"❌ Failed to initialize ChromaDB: {e2}")
            raise e2

    _collection = _chroma.get_or_create_collection("study_notes")
    return _collection


def store_chunks(chunks, doc_id, doc_type="resume"):
    print("🧪 ENTERED store_chunks()")
    print("🧠 Doc ID:", doc_id)
    print("📄 Type:", doc_type)
    print("📄 Number of chunks:", len(chunks))

    if not chunks:
        print("⚠️ No chunks to store")
        return

    col = get_collection()

    ids = []
    metadatas = []

    for i, chunk in enumerate(chunks):
        ids.append(f"{doc_type}_{doc_id}_{i}")
        metadatas.append(
            {
                "doc_id": doc_id,
                "type": doc_type,  # "resume" or "job"
                "chunk_id": i,
                "preview": chunk[:120],
            }
        )

    # Let Chroma handle internal embeddings (defaults to ONNX all-MiniLM-L6-v2)
    col.add(documents=chunks, metadatas=metadatas, ids=ids)

    # Manual persist for older ChromaDB versions
    global _chroma
    if hasattr(_chroma, "persist"):
        _chroma.persist()
        print("💾 Data manually persisted (v0.3.x)")
    else:
        print("💾 Data auto-persisted (v0.4.x)")

    print("📦 Collection count after add:", col.count())
    print("✅ store_chunks() complete\n")


def search_resume(query, resume_id, k=3):
    col = get_collection()

    print(f"🔎 Searching for resume_id={resume_id}")

    # Try strict filter first
    try:
        all_docs = col.get(
            where={"$and": [{"doc_id": resume_id}, {"type": "resume"}]},
            include=["documents"],
        )

        total = len(all_docs["documents"])

        # If nothing found, fallback to doc_id only
        if total == 0:
            print("⚠️ No docs found with type=resume, falling back to doc_id only")
            all_docs = col.get(where={"doc_id": resume_id}, include=["documents"])
            total = len(all_docs["documents"])

    except Exception as e:
        print("❌ Metadata filter failed, fallback:", e)
        all_docs = col.get(where={"doc_id": resume_id}, include=["documents"])
        total = len(all_docs["documents"])

    if total == 0:
        print("❌ No resume chunks found at all")
        return []

    # Cap k safely
    k = min(k, total)
    print(f"📊 Found {total} chunks, requesting top {k}")

    results = col.query(
        query_texts=[query],
        n_results=k,
        where={"doc_id": resume_id},
        include=["documents", "distances", "metadatas"],
    )

    chunks = []
    for i, doc in enumerate(results["documents"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        chunks.append(
            {
                "text": doc,
                "score": round(1 - results["distances"][0][i], 3),
                "chunk_id": meta.get("chunk_id", i),
                "preview": meta.get("preview", doc[:120]),
            }
        )

    return chunks


def search_job(query, job_id, k=3):
    col = get_collection()

    results = col.query(
        query_texts=[query],
        n_results=k,
        where={"$and": [{"doc_id": job_id}, {"type": "job"}]},
        include=["documents", "distances", "metadatas"],
    )

    chunks = []
    if results["documents"] and results["documents"][0]:
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            chunks.append(
                {
                    "text": doc,
                    "score": round(1 - results["distances"][0][i], 3),
                    "chunk_id": meta.get("chunk_id", i),
                    "preview": meta.get("preview", doc[:120]),
                }
            )

    return chunks
