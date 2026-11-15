"""
Modul RAG — mengubah knowledge base menjadi embedding, menyimpannya ke FAISS,
melakukan semantic search, lalu menghasilkan jawaban menggunakan LLM.

Alur ini serupa dengan yang sudah dibangun di Assignment 3 (Semantic Search &
LLM Application) dan Sesi 11 (Building a Simple RAG System) — di assignment ini,
alur tersebut dibungkus menjadi fungsi yang dipanggil oleh router.py.

Alur:
  build_index()  : CSV -> unit retrievable -> embedding (RETRIEVAL_DOCUMENT)
                   -> normalisasi L2 -> FAISS IndexFlatIP   (sekali saat startup)
  semantic_search: query -> embedding (RETRIEVAL_QUERY) -> top-k + skor cosine
  answer_from_knowledge_base: retrieval -> cek relevansi -> prompt
                   (Persona/Task/Context/Format) -> LLM -> jawaban
"""

import logging
import os
from pathlib import Path

import faiss
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()  # membaca .env saat dijalankan lokal; di Docker variabel datang dari --env-file

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE_PATH = BASE_DIR / "data" / "it_helpdesk_knowledge_base.csv"

# Konfigurasi dibaca dari environment variables (tidak ada yang di-hardcode).
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "gemini-embedding-001")
LLM_MODEL = os.environ.get("LLM_MODEL", "gemini-3.5-flash-lite")
# Model cadangan dipakai jika model utama gagal (misal overload / rate limit).
LLM_FALLBACK_MODELS = [LLM_MODEL, "gemini-3.1-flash-lite"]

TOP_K = 3
# Skor cosine minimum agar sebuah FAQ dianggap relevan dengan query.
# Di bawah nilai ini, sistem menyatakan tidak tahu (tidak memanggil LLM),
# sehingga mencegah halusinasi untuk pertanyaan di luar cakupan knowledge base.
RELEVANCE_THRESHOLD = float(os.environ.get("RELEVANCE_THRESHOLD", "0.65"))

NO_ANSWER_MESSAGE = (
    "Maaf, saya tidak menemukan informasi terkait pertanyaan Anda di knowledge base "
    "IT Helpdesk. Silakan hubungi tim IT Support atau HR untuk bantuan lebih lanjut."
)


class LLMError(Exception):
    """Dilempar ketika layanan Gemini (embedding / LLM) gagal merespons."""


_client = None
_index = None
_df = None


def get_client() -> genai.Client:
    """Membuat Gemini client sekali saja (lazy). API key wajib datang dari env var."""
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or api_key == "your_api_key_here":
            raise LLMError("GEMINI_API_KEY belum diatur di environment variables / file .env.")
        _client = genai.Client(api_key=api_key)
    return _client


def _normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype="float32")
    faiss.normalize_L2(vectors)
    return vectors


def _embed(texts: list[str], task_type: str) -> np.ndarray:
    """Membuat embedding untuk beberapa teks sekaligus (satu request batch)."""
    try:
        result = get_client().models.embed_content(
            model=EMBEDDING_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(task_type=task_type),
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError(f"Gagal membuat embedding: {exc}") from exc
    return _normalize([e.values for e in result.embeddings])


def build_index():
    """
    Membangun index FAISS dari knowledge base CSV. Dipanggil sekali saat startup
    aplikasi (lihat lifespan di main.py), bukan di setiap request.

    Unit retrievable = satu baris FAQ (kategori + pertanyaan + jawaban), karena
    setiap FAQ sudah merupakan satu potongan informasi yang utuh dan pendek.
    """
    global _index, _df

    df = pd.read_csv(KNOWLEDGE_BASE_PATH)
    df["text"] = (
        "Kategori: " + df["category"]
        + "\nPertanyaan: " + df["question"]
        + "\nJawaban: " + df["answer"]
    )

    embeddings = _embed(df["text"].tolist(), task_type="RETRIEVAL_DOCUMENT")

    # Vektor sudah dinormalisasi L2, sehingga inner product = cosine similarity.
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)

    _index, _df = index, df
    logger.info("Index FAISS siap: %d dokumen, dimensi %d", index.ntotal, index.d)
    return index


def is_ready() -> bool:
    return _index is not None


def semantic_search(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Mencari top-k FAQ yang paling mirip secara semantik dengan query.
    Mengembalikan list dict berisi faq_id, category, question, answer, score.
    """
    if _index is None:
        build_index()  # lazy fallback jika index belum dibangun saat startup

    query_vec = _embed([query], task_type="RETRIEVAL_QUERY")
    scores, ids = _index.search(query_vec, top_k)

    results = []
    for score, idx in zip(scores[0], ids[0]):
        if idx == -1:
            continue
        row = _df.iloc[idx]
        results.append({
            "faq_id": int(row["faq_id"]),
            "category": row["category"],
            "question": row["question"],
            "answer": row["answer"],
            "score": float(score),
        })
    return results


def build_prompt(query: str, contexts: list[dict]) -> str:
    """Menyusun prompt terstruktur: Persona / Task / Context / Format."""
    context_text = "\n\n".join(
        f"[FAQ #{c['faq_id']} | {c['category']}]\n"
        f"Pertanyaan: {c['question']}\n"
        f"Jawaban: {c['answer']}"
        for c in contexts
    )

    return f"""## Persona
Kamu adalah asisten AI Helpdesk internal untuk tim People & IT Operations perusahaan.
Kamu ramah, profesional, dan hanya memberikan informasi yang sesuai kebijakan resmi perusahaan.

## Task
Jawab pertanyaan karyawan di bawah ini HANYA berdasarkan informasi pada bagian Context.
- Jangan menambahkan informasi, angka, atau kebijakan yang tidak tertulis di Context.
- Jika Context tidak memuat jawaban atas pertanyaan, jawab persis:
  "Maaf, informasi tersebut tidak tersedia di knowledge base. Silakan hubungi tim IT Support atau HR."

## Context
{context_text}

## Format
- Jawab dalam Bahasa Indonesia, singkat dan jelas (maksimal 3-4 kalimat).
- Jika ada langkah-langkah, tuliskan secara berurutan.
- Tanpa salam pembuka panjang dan tanpa menyebut nomor FAQ.

## Pertanyaan Karyawan
{query}
"""


async def generate(prompt: str, temperature: float = 0.2) -> str:
    """
    Memanggil Gemini LLM (async) dengan fallback ke model cadangan.
    Melempar LLMError jika seluruh model gagal atau respons kosong.
    """
    client = get_client()
    last_error = None
    for model in dict.fromkeys(LLM_FALLBACK_MODELS):  # urutan dipertahankan, tanpa duplikat
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
            text = (response.text or "").strip()
            if text:
                return text
            last_error = "respons kosong"
        except Exception as exc:
            last_error = exc
            logger.warning("Model %s gagal: %s", model, exc)
    raise LLMError(f"LLM gagal merespons: {last_error}")


async def answer_from_knowledge_base(query: str) -> str:
    """
    Fungsi yang dipanggil router.py ketika intent = 'faq'.
    Menjalankan semantic search, menyusun prompt dengan context hasil retrieval,
    lalu memanggil LLM untuk menghasilkan jawaban.
    """
    results = semantic_search(query)
    for r in results:
        logger.info("  retrieved FAQ #%d (score=%.3f): %s", r["faq_id"], r["score"], r["question"])

    relevant = [r for r in results if r["score"] >= RELEVANCE_THRESHOLD]
    if not relevant:
        # Tidak ada FAQ yang cukup relevan -> jangan paksa LLM menjawab (cegah halusinasi).
        return NO_ANSWER_MESSAGE

    return await generate(build_prompt(query, relevant))
