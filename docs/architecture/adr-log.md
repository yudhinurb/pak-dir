# Architecture Decision Records (ADR Log)

Setiap keputusan arsitektur penting yang diambil oleh **`@architect`** dan disetujui **CTO** dicatat di halaman ini.

---

## ADR-001: On-Demand Static Checking & Multi-Provider Free LLM Fallback Router
- **Status:** Disetujui (Accepted)
- **Konteks:** Menjalankan model LLM lokal raksasa (7B–70B) secara lokal membutuhkan GPU mahal, sementara menyalakan *Language Server Protocol (LSP)* di background secara terus-menerus memakan ratusan megabyte RAM host saat idle.
- **Keputusan:**
  1. Menonaktifkan *background auto-LSP* (`"lsp": false` di `opencode.json`) dan menggantinya dengan verifikasi *linter on-demand* saat fase `@dev` dan `@ops`.
  2. Mengimplementasikan `FreeLLMRouter` yang merotasi API gratis dari Gemini, Groq, Cerebras, Mistral Codestral, dan OpenRouter (`:free`) saat terbentur rate limit.
- **Konsekuensi:** Beban memori control plane turun drastis menjadi < 250 MB, menyisakan kapasitas penuh host untuk workload aplikasi pengguna.

---

## ADR-002: Penggunaan SQLite WAL Mode untuk Persistence Internal
- **Status:** Disetujui (Accepted)
- **Konteks:** Menjalankan database server terpisah (seperti PostgreSQL atau MySQL) untuk keperluan internal task tracking menambah overhead operasional dan memori saat idle.
- **Keputusan:** Menggunakan **SQLite3 dengan `PRAGMA journal_mode=WAL`** untuk penyimpanan state internal orkestrator dan antrean approval CTO.
- **Konsekuensi:** Pemakaian memori database mendekati ~15 MB dengan konkurensi baca/tulis yang sangat tinggi pada disk lokal.

---

## ADR-003: Real Code Extraction, Automated QA Execution, & Git Merge Abort Safety
- **Status:** Disetujui (Accepted)
- **Konteks:** Sebelumnya, output rekayasa `@dev` hanya disimpan sebagai dokumentasi teks Markdown di `docs/deliverables/` dan evaluasi `@ops` hanya simulasi teks LLM tanpa menjalankan test suite sesungguhnya di host.
- **Keputusan:**
  1. Mengimplementasikan `orchestrator/code_extractor.py` untuk mendeteksi, memvalidasi keamanan path, dan menulis berkas kode sumber/test nyata ke sistem berkas host.
  2. Mengimplementasikan `orchestrator/qa_runner.py` yang mengeksekusi pemeriksaan sintaksis (`py_compile`) dan automated test runner (`unittest`) nyata di subprocess, serta menginjeksikan pesan error dan traceback aktual ke `@dev` jika pengujian gagal (*true self-healing loop*).
  3. Memastikan isolasi Git branch sejak tahap inisiasi dan menambahkan rollback otomatis (`git merge --abort`) jika terjadi konflik merge pada saat approval CTO.
- **Konsekuensi:** PakDir beralih dari sekadar generator dokumentasi menjadi sistem rekayasa perangkat lunak otonom nyata yang menghasilkan berkas produksi teruji di server.
