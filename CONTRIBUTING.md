# Contributing to PakDir

Thank you for your interest in contributing to **PakDir**! As an open-source project dedicated to enabling solo founders and technical executives to govern autonomous AI engineering teams, we welcome contributions from the community.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and professional environment for everyone. Please be respectful, constructive, and collaborative in all communications.

---

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/pak-dir.git
   cd pak-dir
   ```

2. **Create a virtual environment and install dependencies in editable mode:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .
   ```

3. **Run the automated test suite:**
   ```bash
   python -m unittest discover -s tests -p "test_*.py" -v
   ```

---

## Architectural Principles to Preserve

When proposing changes or opening Pull Requests, please adhere to these core design constraints:

1. **Ultra-Low Memory Footprint (<250MB Overhead):**
   - Do not introduce heavy background dependencies or background language servers that consume substantial resident memory.
   - PakDir must run reliably on an entry-level 2GB RAM cloud VPS.

2. **Zero-Cost Multi-Provider Compatibility:**
   - LLM integration must remain model-agnostic and support transparent failover across free-tier inference APIs.

3. **Deterministic Human-in-the-Loop Governance:**
   - Agents must never push or deploy directly to production without explicit approval through the executive approval gate.

---

## Pull Request Guidelines

1. Create a feature branch (`git checkout -b feat/your-feature-name`).
2. Write clean, self-documenting code with type annotations.
3. Ensure all existing and new tests pass (`python -m unittest discover -s tests -p "test_*.py" -v`).
4. Update relevant documentation in `docs/` and `README.md`.
5. Submit your Pull Request with a clear description of the problem and proposed solution.
