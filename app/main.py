from fastapi import FastAPI

app = FastAPI(title="stocks-api")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "stocks-api is running"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
