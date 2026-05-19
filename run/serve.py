from __future__ import annotations

import logging
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from src.api.chat import router as chat_router

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Load skill definitions from skills/ directory at startup
from src.skills import load_skills
load_skills()

app = FastAPI(title="OpenAILearning API", version="0.1.0")

# CORS: read ALLOWED_ORIGINS from .env; allow all by default (development).
# In production, set ALLOWED_ORIGINS=https://yourdomain.com,https://another.com
_origins = os.environ.get("ALLOWED_ORIGINS", "*")
_origin_list = ["*"] if _origins == "*" else [o.strip() for o in _origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(chat_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}


# Static files must be mounted AFTER all API routes
_static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("run.serve:app", host="0.0.0.0", port=8080, reload=True)
