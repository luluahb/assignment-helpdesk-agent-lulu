"""
Router — menentukan apakah query user butuh RAG (informasi/kebijakan)
atau tool call (aksi spesifik, misal cek status tiket).

Pola ini mengikuti workflow router yang sudah dipelajari di Sesi 12.4:
  user input -> intent detection -> jika butuh data -> retrieval (RAG)
                                  -> jika butuh aksi -> tool call

Intent detection memakai pendekatan HYBRID (3 lapis, dari yang paling murah):
  1. Rule: ada ticket_id di query (TICKET-0005, ticket0042, tiket #7)  -> ticket_status
  2. Keyword: kata kerja pengecekan + kata "tiket" ("cek status tiket saya") -> ticket_status
  3. LLM classifier (structured prompt) untuk sisanya -> faq / ticket_status / unknown
     Jika LLM gagal, fallback ke "faq" (RAG tetap punya penjaga relevansi sendiri).
"""

import logging
import re

from app.rag import LLMError, answer_from_knowledge_base, generate
from app.tools import find_ticket, get_ticket_status

logger = logging.getLogger(__name__)

VALID_INTENTS = {"faq", "ticket_status", "unknown"}

# "TICKET-0005", "ticket0042", "TICKET 12", "ticket_7"
_TICKET_ID_PATTERN = re.compile(r"\bticket[\s\-_#]*(\d{1,6})\b", re.IGNORECASE)
# "tiket 5", "tiket no. 0005", "tiket nomor 12", "tiket #7"
_TIKET_NUMBER_PATTERN = re.compile(
    r"\b(?:tiket|ticket)\s*(?:nomor|nomer|no\.?|number|id)?\s*[#:\-]?\s*(\d{1,6})\b",
    re.IGNORECASE,
)

_TICKET_WORDS = ("tiket", "ticket")
_CHECK_WORDS = (
    "status", "cek", "check", "lacak", "track", "progres", "progress",
    "update", "sampai mana", "sudah diproses", "bagaimana kabar",
)

INTENT_PROMPT = """Kamu adalah intent classifier untuk asisten IT Helpdesk internal perusahaan.

Klasifikasikan pesan karyawan ke dalam SATU kategori berikut:
- faq: pertanyaan informasi/kebijakan/prosedur umum tentang IT, HR, cuti, absensi, akses, password, perangkat, onboarding, payroll, kebijakan kerja, atau tools kantor. Termasuk pertanyaan "bagaimana cara membuat tiket".
- ticket_status: permintaan mengecek status/progres tiket helpdesk tertentu milik user.
- unknown: sapaan kosong, pesan tidak jelas, atau topik di luar lingkup IT/HR perusahaan (cuaca, resep, olahraga, berita, matematika, dll).

Jawab HANYA dengan satu kata: faq, ticket_status, atau unknown.

Pesan: {query}
Kategori:"""


def extract_ticket_id(query: str) -> str | None:
    """
    Mengekstrak ticket_id dari teks query dan menormalkannya ke format data
    pada mock_tickets.csv, yaitu 'TICKET-XXXX' (4 digit, zero-padded).
    Contoh: "ticket0042" -> "TICKET-0042", "tiket no 7" -> "TICKET-0007".
    Mengembalikan None jika tidak ada ticket_id di query.
    """
    match = _TICKET_ID_PATTERN.search(query) or _TIKET_NUMBER_PATTERN.search(query)
    if not match:
        return None
    return f"TICKET-{int(match.group(1)):04d}"


def _keyword_ticket_intent(query: str) -> bool:
    q = query.lower()
    return any(w in q for w in _TICKET_WORDS) and any(w in q for w in _CHECK_WORDS)


def _parse_intent(raw: str) -> str:
    """Membersihkan output LLM (misal 'Faq.', '**ticket_status**') menjadi label valid."""
    cleaned = re.sub(r"[^a-z_]", "", raw.strip().split()[0].lower()) if raw.strip() else ""
    return cleaned if cleaned in VALID_INTENTS else "unknown"


async def detect_intent(query: str) -> str:
    """
    Menentukan intent dari query user. Mengembalikan salah satu:
      - "faq"           -> pertanyaan informasi/kebijakan, arahkan ke RAG
      - "ticket_status" -> permintaan cek status tiket, arahkan ke tool call
      - "unknown"       -> di luar cakupan / tidak dikenali
    """
    # Lapis 1: ticket_id eksplisit -> pasti butuh pengecekan data.
    if _TICKET_ID_PATTERN.search(query):
        logger.info("intent=ticket_status (rule: ticket_id terdeteksi)")
        return "ticket_status"

    # Lapis 2: keyword cek + tiket.
    if _keyword_ticket_intent(query):
        logger.info("intent=ticket_status (rule: keyword)")
        return "ticket_status"

    # Lapis 3: LLM classifier.
    try:
        raw = await generate(INTENT_PROMPT.format(query=query), temperature=0.0)
        intent = _parse_intent(raw)
        logger.info("intent=%s (LLM, raw=%r)", intent, raw)
        return intent
    except LLMError as exc:
        logger.warning("LLM intent detection gagal (%s), fallback ke 'faq'", exc)
        return "faq"


async def handle_query(query: str) -> dict:
    """
    Fungsi utama yang dipanggil oleh endpoint /ask.
    Menjalankan intent detection, lalu merutekan ke RAG atau tool call yang sesuai.
    """
    intent = await detect_intent(query)

    if intent == "ticket_status":
        ticket_id = extract_ticket_id(query)
        data = get_ticket_status(ticket_id)
        if ticket_id is None:
            status = "missing_ticket_id"
        elif find_ticket(ticket_id) is None:
            status = "not_found"
        else:
            status = "success"
        return {"status": status, "intent": "ticket_status", "data": data}

    elif intent == "faq":
        data = await answer_from_knowledge_base(query)
        return {"status": "success", "intent": "faq", "data": data}

    else:
        return {
            "status": "error",
            "intent": "unknown",
            "data": (
                "Maaf, saya tidak memahami permintaan Anda atau permintaan tersebut di luar "
                "cakupan IT Helpdesk. Saya dapat membantu pertanyaan seputar kebijakan IT/HR "
                "(cuti, akses, password, perangkat, dll.) atau pengecekan status tiket "
                "(contoh: 'cek status TICKET-0005')."
            ),
        }
