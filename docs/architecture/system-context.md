# 1. System Context & Container Diagram

> Dikelola secara otomatis oleh **`@architect`**. Setiap penambahan *service*, database, atau integrasi eksternal wajib tercermin pada diagram di bawah ini.

## Diagram Konteks & Container (C4 Level 1 & 2)

```mermaid
flowchart LR
    CTO["👨‍💻 CTO (Executive)\nDiscord & Web Browser"]
    Discord["💬 Discord Cloud\nVirtual Office"]
    LLM["🧠 Multi-Provider Free Inference Pool\nGemini + Groq + Cerebras + Codestral + OpenRouter"]

    subgraph Host["🖥️ Host Environment (Cloud VPS / Local Workstation)"]
        Orch["🤖 PakDir Control Plane & Router\n(Python Async + SQLite WAL)"]
        OC["⚙️ OpenCode Engine\n(Core Team + Dynamic Specialists)"]
        Portal["🌐 Live Architecture Portal\n(Caddy + Docsify)"]
        App["🚀 Production Application Workload\n(Alokasi Penuh Sisa Memori Host)"]
    end

    CTO <-->|"Perintah & !approve"| Discord
    Discord <--> Orch
    Orch <--> OC
    OC <-->|"Rotasi API Otomatis"| LLM
    OC -->|"Update Markdown & Mermaid"| Portal
    OC -->|"Build, Test & Deploy"| App
    CTO -->|"Pantau Visual"| Portal
```
