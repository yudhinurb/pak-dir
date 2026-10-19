# 🏢 PakDir Architecture & Live AI Org-Chart Portal

Selamat datang di **PakDir Architecture & Live AI Org-Chart Portal**.
Seluruh halaman dan diagram di portal ini diperbarui secara otomatis oleh **`@architect` (Principal System Architect)** dan **`@em` (Engineering Manager / Agent Factory)** setiap kali ada fitur baru atau perubahan tim.

## Ringkasan Arsitektur & Alokasi Sumber Daya (Resource-Aware Control Plane)

| Lapis Arsitektur | Alokasi Memori Kontrol | Komponen Utama |
| :--- | :--- | :--- |
| **Sistem Operasi & Host** | Dinamis | Linux / macOS / Windows Runtime |
| **PakDir Control Plane** | < 250 MB | OpenCode Engine (On-Demand LSP) + Discord Bridge + Free Router |
| **Live Architecture Portal** | ~20 MB | Caddy Web Server + Docsify Static Markdown/Mermaid |
| **State Storage & Audit** | ~15 MB | SQLite3 (`WAL Mode`) di `data/company_state.db` |
| **Alokasi Workload Produk** | **Mayoritas Sumber Daya Host** | **Jatah penuh untuk aplikasi teknologi yang sedang dibangun** |

## Cara Cepat CTO Berinteraksi (Via Discord)
- `!status` — Dashboard real-time kesehatan host, alert aktif Sentinel, dan antrean task.
- `!hotfix <instruksi>` — Menugaskan perbaikan file spesifik dengan validasi sintaks terisolasi dan antrean approval deploy ke branch `main`.
- `!investigate <service|issue>` — Analisis akar masalah service bermasalah atau log error via LLM.
- `!ask <nama_agent> <pertanyaan>` — Bertanya langsung ke karyawan AI mana pun (contoh: `!ask architect jelaskan skema autentikasi saat ini`).
- `!agents` — Melihat daftar karyawan AI aktif saat ini.
- `!hire <nama> <instruksi>` — Melahirkan agen spesialis baru secara instan.
- `!specialize <nama> <fokus_baru>` — Mempertajam fokus kerja agen yang sudah ada.
- `!pending` / `!approve <id>` / `!reject <id> <alasan>` — Mengontrol gerbang *deployment* ke produksi.
- `!health` — Memeriksa telemetri penggunaan RAM, Swap, dan Disk host secara *real-time*.

> 📖 **Panduan Deployment Hardware Spesifik:**  
> Untuk panduan deployment dan rekomendasi spesifikasi perangkat keras (dari VPS minimal hingga server besar), lihat [Self-Hosting & Node Sizing Guide](deployment/self-hosting.md).
