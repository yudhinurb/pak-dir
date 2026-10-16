# Self-Hosting & Node Deployment Guide

This guide describes how to deploy and operate the **PakDir** control plane on Linux cloud servers, virtual private servers (VPS), or dedicated hardware.

---

## Hardware Sizing & System Requirements

PakDir's resource-aware control plane consumes less than 250MB resident memory. Sizing depends primarily on the software products your AI engineering team builds and tests.

| Tier | Specifications | Target Workloads |
| :--- | :--- | :--- |
| **Minimum** | 1 vCPU, 2GB RAM (+ 4GB NVMe Swap) | Lightweight microservices, APIs, prototypes, CLI tools |
| **Recommended** | 2 vCPU, 4GB RAM | Full-stack web applications, relational databases, comprehensive test suites |
| **Enterprise** | 4+ vCPU, 8GB+ RAM | Multi-container applications, data pipelines, high-concurrency staging environments |

---

## Automated Deployment (`setup-host.sh`)

PakDir includes a single host initialization script:

```bash
sudo bash deploy/setup-host.sh
```

### Intelligent System Optimization:
The script automatically inspects the host's physical memory:
- **Low-Memory Hosts (< 4GB RAM):** Automatically provisions a 4GB Swap file at `/swapfile`, configures `vm.swappiness=10`, and enables `zram-tools` for transparent memory compression.
- **Higher-Spec Hosts (>= 4GB RAM):** Preserves existing swap configurations without unnecessary disk overhead.
- **Core Runtime:** Installs `caddy`, `sqlite3`, `python3-venv`, and `opencode`.
- **Process Management:** Configures `pakdir-orchestrator.service` with a safe resident memory ceiling (`MemoryMax=250M`).

---

## Post-Installation Configuration

1. **Environment Variables:**
   ```bash
   cp .env.example .env
   nano .env
   ```
   Provide your `DISCORD_BOT_TOKEN` and at least two free LLM API keys.

2. **Start the Control Plane:**
   ```bash
   sudo systemctl restart pakdir-orchestrator
   ```

3. **Verify Node Health:**
   In your Discord channel, type `!health` to verify that memory, swap, and model pools are operating normally.
