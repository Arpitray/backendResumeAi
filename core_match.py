import numpy as np
from memory import get_collection, cosine_similarity


def run_match(resume_id, job_id):
    col = get_collection()

    resume_data = col.get(
        where={"$and": [{"doc_id": resume_id}, {"type": "resume"}]},
        include=["documents", "embeddings"],
    )

    job_data = col.get(
        where={"$and": [{"doc_id": job_id}, {"type": "job"}]},
        include=["documents", "embeddings"],
    )

    if not resume_data["documents"]:
        return None, "Resume not found"

    if not job_data["documents"]:
        return None, "Job not found"

    resume_embs = resume_data["embeddings"]
    job_embs = job_data["embeddings"]

    resume_docs = resume_data["documents"]
    job_docs = job_data["documents"]

    scores = []
    best_matches = []

    for i, r_emb in enumerate(resume_embs):
        best_score = 0
        best_job_chunk = ""

        for j, j_emb in enumerate(job_embs):
            score = cosine_similarity(r_emb, j_emb)
            if score > best_score:
                best_score = score
                best_job_chunk = job_docs[j]

        scores.append(best_score)
        best_matches.append(
            {
                "resume_chunk": resume_docs[i][:160],
                "job_match": best_job_chunk[:160],
                "score": round(float(best_score), 3),
            }
        )

    avg_score = float(np.mean(scores))
    percent = round(avg_score * 100, 2)

    top_matches = sorted(best_matches, key=lambda x: x["score"], reverse=True)[:3]

    return {
        "match_score_percent": percent,
        "resume_chunks": len(resume_embs),
        "job_chunks": len(job_embs),
        "top_matches": top_matches,
    }, None
