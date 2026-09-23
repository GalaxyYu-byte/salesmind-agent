from fastapi import FastAPI

app = FastAPI(title="SalesMind Agent")


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}