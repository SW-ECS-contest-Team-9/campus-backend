from fastapi import FastAPI
from data import router

app = FastAPI()
app.include_router(router)



@app.get("/")
def main():
    return {"message":"Campus Backend"}
