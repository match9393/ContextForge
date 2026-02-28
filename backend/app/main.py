from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="ContextForge Backend", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "app_env": settings.app_env,
        "providers": {
            "answer": settings.answer_provider,
            "vision": settings.vision_provider,
            "embeddings": settings.embeddings_provider,
        },
    }


@app.get("/api/v1/health")
def api_health() -> dict:
    return {"status": "ok", "service": "backend"}
