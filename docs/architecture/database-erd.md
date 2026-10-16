# 3. Skema Database (Entity-Relationship Diagram)

> Halaman ini mendokumentasikan skema database internal perusahaan (`data/company_state.db`) serta tabel database produk yang dirancang oleh **`@architect`**.

## Skema Database State Perusahaan (`company_state.db`)

```mermaid
erDiagram
    TASKS {
        int id PK
        string title
        string instruction
        string branch_name
        string status
        string current_stage
        int retry_count
        string summary
        string cto_feedback
        float created_at
        float updated_at
    }

    AGENT_EVENTS {
        int id PK
        string action
        string agent_name
        string details
        float created_at
    }

    LLM_ROTATIONS {
        int id PK
        string agent_name
        string failed_model
        string selected_model
        string reason
        float created_at
    }
```
