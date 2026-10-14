import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import errors
from routers import data, health, maps

DEV_ORIGINS = "http://localhost:8081,http://127.0.0.1:8081,http://localhost:19006,http://localhost:5173,http://127.0.0.1:5173"

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", DEV_ORIGINS).split(",") if o.strip()],
    allow_methods=["GET"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)
errors.install(app)
app.include_router(data.router)
app.include_router(health.router)
app.include_router(maps.router)


@app.get("/")
def main():
    return {"message":"Campus Backend"}
