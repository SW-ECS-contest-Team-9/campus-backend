from fastapi import APIRouter

from database import connect

router = APIRouter()


@router.get("/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        db = "ok"
    except Exception:  # DB 가 꺼져도 health 는 답한다
        db = "down"
    return {"status": "ok" if db == "ok" else "degraded", "db": db}
