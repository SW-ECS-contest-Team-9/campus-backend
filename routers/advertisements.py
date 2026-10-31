from fastapi import APIRouter, Depends
from psycopg import Connection

from advertisements import repository
from database import get_conn

router = APIRouter(prefix="/v1")


@router.get("/advertisements")
def advertisements(conn: Connection = Depends(get_conn)):
    items = [
        {
            "id": row["id"],
            "title": row["title"],
            "imageUrl": row["image_url"],
            "targetUrl": row["target_url"],
            "startsAt": row["starts_at"],
            "endsAt": row["ends_at"],
            "isActive": row["is_active"],
        }
        for row in repository.list_active(conn)
    ]
    return {"count": len(items), "items": items}
