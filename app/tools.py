"""
Modul Tools — fungsi-fungsi "aksi" yang dipanggil router ketika intent
BUKAN pertanyaan informasi/kebijakan, melainkan permintaan spesifik
yang butuh pengecekan data langsung (bukan retrieval semantik).

Pola ini mengikuti konsep Tool Integration di Sesi 12.3.

Kenapa tool call, bukan RAG? Status tiket adalah data terstruktur yang harus
dicocokkan secara EXACT berdasarkan ticket_id. Semantic search bisa saja
mengembalikan tiket yang "mirip" (misal TICKET-0005 untuk query TICKET-0050),
dan itu jawaban yang salah. Lookup langsung ke tabel menjamin jawaban akurat.
"""

from functools import lru_cache
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
MOCK_TICKETS_PATH = BASE_DIR / "data" / "mock_tickets.csv"


@lru_cache(maxsize=1)
def _load_tickets() -> pd.DataFrame:
    """Memuat mock_tickets.csv sekali saja (di-cache), dengan ticket_id sebagai index."""
    df = pd.read_csv(MOCK_TICKETS_PATH, dtype=str)
    df["ticket_id"] = df["ticket_id"].str.strip().str.upper()
    return df.set_index("ticket_id")


def find_ticket(ticket_id: str | None) -> dict | None:
    """Mengembalikan data tiket sebagai dict, atau None jika tidak ditemukan."""
    if not ticket_id:
        return None
    tickets = _load_tickets()
    ticket_id = ticket_id.strip().upper()
    if ticket_id not in tickets.index:
        return None
    return {"ticket_id": ticket_id, **tickets.loc[ticket_id].to_dict()}


def get_ticket_status(ticket_id: str | None) -> str:
    """
    Mencari status tiket berdasarkan ticket_id pada data mock_tickets.csv.

    Tidak pernah melempar exception untuk input yang tidak valid: jika ticket_id
    kosong atau tidak ditemukan, fungsi mengembalikan pesan yang informatif.
    """
    if not ticket_id:
        return (
            "Nomor tiket tidak ditemukan pada pesan Anda. "
            "Mohon sertakan nomor tiket dengan format TICKET-XXXX (contoh: TICKET-0005)."
        )

    row = find_ticket(ticket_id)
    if row is None:
        return (
            f"Tiket {ticket_id.strip().upper()} tidak ditemukan di sistem. "
            "Mohon periksa kembali nomor tiket Anda atau hubungi tim IT Support."
        )

    return (
        f"Tiket {row['ticket_id']} ({row['category']}): {row['subject']}. "
        f"Status: {row['status']}. "
        f"Terakhir diperbarui: {row['last_update']}. "
        f"Pemohon: {row['employee_name']}."
    )
