"""
Pengujian end-to-end endpoint /ask dengan beberapa skenario.

Cara pakai (server harus sudah berjalan, lokal maupun di container Docker):
    python tests/test_scenarios.py                          # default http://127.0.0.1:8000
    python tests/test_scenarios.py http://localhost:8000

Hanya memakai standard library, sehingga tidak butuh dependency tambahan.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"

# (nama skenario, query, intent yang diharapkan)
SCENARIOS = [
    ("RAG #1 - kebijakan cuti", "Berapa jatah cuti tahunan saya sebagai karyawan tetap?", "faq"),
    ("RAG #2 - reset password VPN", "Gimana cara reset password VPN kantor?", "faq"),
    ("Tool #1 - tiket ada", "Tolong cek status tiket TICKET-0005 saya dong", "ticket_status"),
    ("Tool #2 - tiket tidak ada", "Apa status tiket TICKET0042 saya?", "ticket_status"),
    ("Ambigu / di luar cakupan", "Bagaimana cuaca di Jakarta hari ini?", "unknown"),
    # Skenario tambahan
    ("Tambahan - tiket tanpa ID", "Tiket saya sudah sampai mana ya statusnya?", "ticket_status"),
    ("Tambahan - FAQ tidak ada di KB", "Apakah kantor menyediakan fasilitas gym?", "faq"),
    ("Tambahan - query kosong", "   ", "unknown"),
]


def ask(query: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE_URL}/ask",
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def main():
    print(f"Target: {BASE_URL}\n")
    passed = 0
    for i, (name, query, expected) in enumerate(SCENARIOS, 1):
        start = time.perf_counter()
        code, body = ask(query)
        elapsed = time.perf_counter() - start
        ok = body.get("intent") == expected
        passed += ok
        print(f"[{i}] {name}  {'PASS' if ok else 'FAIL'}  ({elapsed:.1f}s)")
        print(f"    query    : {query!r}")
        print(f"    expected : {expected}")
        print(f"    HTTP {code} -> {json.dumps(body, ensure_ascii=False)}\n")
    print(f"Routing benar: {passed}/{len(SCENARIOS)}")


if __name__ == "__main__":
    main()
