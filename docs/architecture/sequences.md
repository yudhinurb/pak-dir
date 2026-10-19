# 2. Alur Kerja Sistem & Sequence Diagram

> Dokumentasi alur eksekusi fitur, *self-healing loop*, serta alur endpoint aplikasi utama.

## Alur Eksekusi Fitur & Gerbang Approval CTO

```mermaid
flowchart TD
    S1["1. CTO kirim perintah !hotfix di Discord"]
    S2["2. Gatekeeper isolasi branch & analisis target file"]
    S3["3. @dev generate patch & @ops verifikasi sintaks/linter"]
    S4["4. Simpan patch & buat commit di branch hotfix"]
    S5{"5. Syntax QA Lulus?"}
    SFix["Ulangi koreksi patch"]
    S6["6. Masuk antrean WAITING_CTO_APPROVAL"]
    S7["7. CTO ketik !approve -> Git merge ke main & deploy"]

    S1 --> S2
    S2 --> S3
    S3 --> S4
    S4 --> S5
    S5 -->|"Gagal"| SFix
    SFix --> S3
    S5 -->|"Lulus"| S6
    S6 --> S7
```
