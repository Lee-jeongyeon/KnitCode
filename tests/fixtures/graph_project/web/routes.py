from fastapi import APIRouter

router = APIRouter()


@router.post("/users")
async def create_user():
    return {}
