from fastapi import FastAPI

from app.database import Base, engine
from app.routers import jobs

app = FastAPI(title="AI-Powered Transaction Processing Pipeline")


@app.on_event("startup")
def on_startup():
    # create_all is fine for an assignment-scope project; a production system
    # would use Alembic migrations so schema changes are versioned and reviewable.
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(jobs.router)
