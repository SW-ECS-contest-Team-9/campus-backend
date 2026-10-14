from fastapi import APIRouter

router = APIRouter()


@router.get("/data")
def data():
    return {"message": "data area"}
