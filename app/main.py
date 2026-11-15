"""
Entry point aplikasi FastAPI — IT Helpdesk Assistant.

Alur endpoint utama:
  1. Menerima pertanyaan/permintaan dari user
  2. Meneruskan ke router (router.py) untuk menentukan intent
  3. Router memanggil RAG (rag.py) atau tool (tools.py) sesuai intent
  4. Mengembalikan response terstruktur ke client

Error handling:
  - Query kosong/tidak valid      -> 422 (status="error", intent="unknown")
  - LLM / embedding gagal         -> 503 (status="error")
  - Error tak terduga lainnya     -> 500 (status="error")
  - Intent tidak dikenali         -> 200 (status="error", intent="unknown"), ditangani router
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import rag
from app.rag import LLMError
from app.router import handle_query

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Membangun index FAISS sekali saat startup, bukan di setiap request."""
    try:
        rag.build_index()
    except Exception as exc:
        # Aplikasi tetap hidup (endpoint tiket tetap bisa dipakai); index akan
        # dicoba dibangun ulang secara lazy pada request FAQ berikutnya.
        logger.error("Gagal membangun index FAISS saat startup: %s", exc)
    yield


app = FastAPI(title="IT Helpdesk Assistant API", lifespan=lifespan)


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000, examples=["Berapa jatah cuti tahunan saya?"])


class AskResponse(BaseModel):
    status: str
    intent: str
    data: str


def _error(http_status: int, message: str, intent: str = "unknown") -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"status": "error", "intent": intent, "data": message},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return _error(422, "Request tidak valid. Kirim JSON dengan format {\"query\": \"pertanyaan Anda\"}.")


@app.get("/health")
async def health():
    """Endpoint sederhana untuk memastikan service berjalan."""
    return {"status": "ok", "index_ready": rag.is_ready()}


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest):
    """
    Endpoint utama. Menerima pertanyaan bebas dari user (bisa berupa
    pertanyaan informasi/kebijakan ATAU permintaan cek status tiket),
    lalu meneruskannya ke router untuk diproses.
    """
    query = request.query.strip()
    if not query:
        return _error(422, "Query tidak boleh kosong.")

    logger.info("Query masuk: %r", query)
    try:
        result = await handle_query(query)
    except LLMError as exc:
        logger.error("LLM error: %s", exc)
        return _error(
            503,
            "Layanan AI sedang tidak dapat merespons. Silakan coba beberapa saat lagi.",
            intent="faq",
        )
    except Exception:
        logger.exception("Error tak terduga saat memproses query")
        return _error(500, "Terjadi kesalahan internal pada server. Silakan coba lagi.")

    logger.info("Response: status=%s intent=%s", result["status"], result["intent"])
    return AskResponse(**result)
