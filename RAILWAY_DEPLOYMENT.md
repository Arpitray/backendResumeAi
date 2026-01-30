# 🚂 Railway Deployment Guide

## ✅ Persistence Fixed

### What Was The Problem?
Previously, the app used `chromadb.Client()` with `Settings(persist_directory=...)`, which could lose data on restart in some environments.

### The Fix
Now using `chromadb.PersistentClient()` which:
- ✅ Auto-persists all data to disk immediately
- ✅ Survives container restarts
- ✅ Works reliably on Railway, Render, and other platforms

### Technical Details
```python
# OLD (could lose data)
_chroma = chromadb.Client(
    Settings(persist_directory="./vector_store")
)

# NEW (guaranteed persistence)
_chroma = chromadb.PersistentClient(
    path="./vector_store",
    settings=Settings(anonymized_telemetry=False)
)
```

### Verification
After uploading a resume, check the logs:
```
💾 Data will auto-persist to ./vector_store
📦 Collection count after add: 12
✅ store_chunks() complete
```

---

## 🚀 Railway Deployment Checklist

### 1. Environment Variables
Set these in Railway dashboard:

```bash
# Required
REDIS_URL=redis://your-redis-url:6379

# Optional (if using external LLM APIs)
OPENAI_API_KEY=your-key-here
ANTHROPIC_API_KEY=your-key-here
```

### 2. Volume Mount (CRITICAL for persistence)
Railway needs a volume mount for the vector_store to survive deployments:

**In Railway Dashboard:**
1. Go to your service → **Variables** tab
2. Add a **Volume Mount**:
   - **Mount Path**: `/app/vector_store`
   - **Size**: 1GB (adjust based on your needs)

This ensures uploaded resumes and job descriptions persist across deploys.

### 3. Start Command
Railway should auto-detect from `Procfile`:
```
web: uvicorn server:app --host 0.0.0.0 --port $PORT
```

Or set manually in Railway:
```bash
uvicorn server:app --host 0.0.0.0 --port $PORT
```

### 4. Health Check
Railway will hit `/` endpoint:
```json
{"status": "AI Career Agent running"}
```

---

## 📊 File Structure After Deploy

```
/app/
├── server.py
├── memory.py
├── core_match.py
├── vector_store/          # ← Persisted (with volume mount)
│   ├── chroma.sqlite3     # ← ChromaDB database
│   └── index/             # ← Vector indices
└── uploads/               # ← Temp PDF storage (optional persistence)
```

---

## 🐛 Troubleshooting

### "Resume not found" after restart
**Cause:** Volume not mounted  
**Fix:** Add volume mount in Railway settings (see above)

### Redis connection errors
**Cause:** `REDIS_URL` not set  
**Fix:** Add Redis add-on in Railway or use external Redis

### Slow first request
**Normal:** Chroma loads the vector index on first access (1-2s)  
**After first load:** Fast lookups

---

## 🧪 Testing Persistence Locally

```bash
# 1. Upload a resume
curl -X POST http://localhost:8000/upload-resume \
  -F "file=@resume.pdf"

# Response: {"stored_as": "abc-123.pdf", ...}

# 2. Restart server
# Ctrl+C then: uvicorn server:app --reload

# 3. Try matching (should still work!)
curl -X POST http://localhost:8000/match \
  -H "Content-Type: application/json" \
  -d '{"resume_id": "abc-123.pdf", "job_id": "xyz"}'
```

If it returns match results → ✅ Persistence works!

---

## 📈 Monitoring

### Check Vector Store Size
```bash
# On Railway shell
du -sh /app/vector_store
```

### Check Collection Count
```python
from memory import get_collection
col = get_collection()
print(f"Total vectors: {col.count()}")
```

---

## 🔐 Security Notes

1. **uploads/** folder contains user PDFs
   - Consider using cloud storage (S3/Cloudinary) for production
   - Or mount a volume and clean periodically

2. **vector_store/** contains embeddings only
   - Original text is chunked but not sensitive
   - No PII stored unless in resumes themselves

3. **Redis cache** expires after 15 minutes
   - Consider shorter TTL for sensitive data
   - Or don't cache match results (only cache AI analysis)

---

## 💡 Production Recommendations

### For High Traffic
1. Use Railway's **horizontal scaling**
2. Add a load balancer
3. Use **PostgreSQL + pgvector** instead of ChromaDB file-based storage

### For Cost Optimization
1. Lazy-load embedding models (already implemented)
2. Cache aggressively (already implemented)
3. Use Railway's sleep mode for non-peak hours

### For Better UX
1. Add WebSocket for live progress updates
2. Stream LLM responses instead of waiting
3. Pre-compute popular job matches

---

## ✅ Deployment Verified

- ✅ PersistentClient configured
- ✅ Auto-persistence enabled
- ✅ Redis caching implemented
- ✅ Parallel LLM calls optimized
- ✅ Ready for Railway deployment

**Next Step:** Push to GitHub, connect Railway, add volume mount, deploy! 🚀
