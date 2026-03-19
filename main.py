from fastapi import FastAPI

app = FastAPI()

# Health check endpoint
@app.get("/")
def read_root():
    return {"status": "ok", "message": "FastAPI is running"}