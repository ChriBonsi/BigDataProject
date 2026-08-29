from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def read_root():
    """Return the API health status."""
    return {"status": "ok", "message": "FastAPI is running"}
