import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.state_db import StateDB

CORE_PROTECTED_AGENTS = {"em", "pm", "architect", "dev", "ops"}
VALID_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
SAFE_TOOLS = {"write", "edit", "bash", "task"}


class AgentFactory:
    """
    Dynamic Agent Factory (HR & Org Builder untuk EM-Agent dan CTO).
    Memungkinkan penambahan karyawan AI spesialis baru (`hire_agent`),
    modifikasi/penajaman tugas karyawan AI (`specialize_agent`),
    pengarsipan agen sementara (`retire_agent`), dan pembaruan otomatis
    diagram struktur organisasi di `docs/architecture/org-chart.md`.
    """

    def __init__(
        self,
        root_dir: str | Path = ".",
        state_db: Optional[StateDB] = None,
    ) -> None:
        self.root_dir = Path(root_dir).resolve()
        self.opencode_json_path = self.root_dir / "opencode.json"
        self.agents_dir = self.root_dir / ".opencode" / "agent"
        self.archive_dir = self.agents_dir / "archive"
        self.org_chart_path = self.root_dir / "docs" / "architecture" / "org-chart.md"
        self.state_db = state_db or StateDB(self.root_dir / "data" / "company_state.db")

        self.agents_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.org_chart_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Dict[str, Any]:
        if not self.opencode_json_path.exists():
            raise FileNotFoundError(f"Config tidak ditemukan: {self.opencode_json_path}")
        with open(self.opencode_json_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_config(self, config: Dict[str, Any]) -> None:
        with open(self.opencode_json_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")

    @staticmethod
    def _validate_agent_name(name: str) -> str:
        clean = name.strip().lower()
        if not VALID_NAME_PATTERN.match(clean):
            raise ValueError(
                f"Nama agent '{name}' tidak valid. Gunakan huruf kecil, angka, '-' atau '_' (2-31 karakter)."
            )
        return clean

    def list_agents(self) -> List[Dict[str, Any]]:
        config = self._load_config()
        agents_cfg = config.get("agent", {})
        result: List[Dict[str, Any]] = []
        for name, meta in agents_cfg.items():
            enabled_tools = [t for t, allowed in meta.get("tools", {}).items() if allowed]
            result.append(
                {
                    "name": name,
                    "description": meta.get("description", ""),
                    "mode": meta.get("mode", "subagent"),
                    "tools": enabled_tools,
                    "is_core": name in CORE_PROTECTED_AGENTS,
                    "prompt_file": f".opencode/agent/{name}.md",
                }
            )
        return result

    def hire_agent(
        self,
        name: str,
        title: str,
        description: str,
        sop_rules: str,
        allowed_tools: Optional[List[str]] = None,
        mode: str = "subagent",
    ) -> Dict[str, Any]:
        """
        Melahirkan karyawan AI spesialis baru:
        1. Membuat file `.opencode/agent/<name>.md`
        2. Mendaftarkan ke `opencode.json`
        3. Meng-update `docs/architecture/org-chart.md`
        """
        clean_name = self._validate_agent_name(name)
        allowed_set = set(allowed_tools or ["bash"]) & SAFE_TOOLS
        # Sub-agent biasa tidak boleh memanggil sub-agent lain kecuali EM
        if clean_name != "em":
            allowed_set.discard("task")

        tools_map = {
            "write": "write" in allowed_set,
            "edit": "edit" in allowed_set,
            "bash": "bash" in allowed_set,
        }

        prompt_md = (
            f"# Role: {title} (`@{clean_name}`)\n\n"
            f"Elu adalah **{title}** yang direkrut secara dinamis oleh `EM-Agent` / **CTO**.\n"
            f"**Deskripsi Peran:** {description}\n\n"
            f"## Standar Operasional Prosedur (SOP) Spesifik:\n"
            f"{sop_rules.strip()}\n\n"
            f"## Standar Efisiensi Sumber Daya & Keamanan Produksi:\n"
            f"1. Selalu jaga efisiensi memori (hindari proses background berat atau dependensi raksasa).\n"
            f"2. Laporkan temuan, perubahan, atau rekomendasi secara terstruktur kepada `EM-Agent` dan CTO.\n"
            f"3. Jangan pernah melakukan merge langsung ke branch `main` tanpa persetujuan CTO.\n"
        )

        agent_file = self.agents_dir / f"{clean_name}.md"
        agent_file.write_text(prompt_md, encoding="utf-8")

        config = self._load_config()
        if "agent" not in config:
            config["agent"] = {}

        config["agent"][clean_name] = {
            "description": f"{title} - {description}",
            "mode": mode if mode in ("subagent", "all", "primary") else "subagent",
            "prompt": f"{{file:.opencode/agent/{clean_name}.md}}",
            "tools": tools_map,
        }
        self._save_config(config)

        self.state_db.log_agent_event(
            action="HIRE_AGENT",
            agent_name=clean_name,
            details={"title": title, "description": description, "tools": list(allowed_set)},
        )
        self.regenerate_org_chart()
        return config["agent"][clean_name]

    def specialize_agent(
        self,
        name: str,
        additional_focus: str,
        new_description: Optional[str] = None,
        replace_specialization: bool = False,
    ) -> Dict[str, Any]:
        """
        Memodifikasi atau mempertajam fokus tugas agen yang sudah ada
        tanpa merusak SOP inti perusahaan.
        """
        clean_name = self._validate_agent_name(name)
        config = self._load_config()
        if clean_name not in config.get("agent", {}):
            raise KeyError(f"Agent '@{clean_name}' belum terdaftar di opencode.json.")

        agent_file = self.agents_dir / f"{clean_name}.md"
        if not agent_file.exists():
            raise FileNotFoundError(f"File SOP agent tidak ditemukan: {agent_file}")

        current_content = agent_file.read_text(encoding="utf-8")
        marker = "\n## Spesialisasi & Fokus Tambahan Aktif:\n"

        if replace_specialization and marker in current_content:
            base_content = current_content.split(marker)[0].rstrip()
        else:
            base_content = current_content.rstrip()

        if marker in base_content:
            updated_content = f"{base_content}\n- {additional_focus.strip()}\n"
        else:
            updated_content = f"{base_content}\n{marker}- {additional_focus.strip()}\n"

        agent_file.write_text(updated_content, encoding="utf-8")

        if new_description:
            config["agent"][clean_name]["description"] = new_description.strip()
            self._save_config(config)

        self.state_db.log_agent_event(
            action="SPECIALIZE_AGENT",
            agent_name=clean_name,
            details={"focus": additional_focus.strip(), "replace": replace_specialization},
        )
        self.regenerate_org_chart()
        return {
            "name": clean_name,
            "updated_focus": additional_focus.strip(),
            "prompt_file": str(agent_file.relative_to(self.root_dir)),
        }

    def retire_agent(self, name: str) -> None:
        """
        Menonaktifkan dan mengarsipkan agen spesialis sementara.
        Agen inti (em, pm, architect, dev, ops) dilindungi agar tidak terhapus.
        """
        clean_name = self._validate_agent_name(name)
        if clean_name in CORE_PROTECTED_AGENTS:
            raise PermissionError(
                f"Agent inti '@{clean_name}' dilindungi dan tidak boleh dipensiunkan. Gunakan specialize_agent() untuk mengubah tugasnya."
            )

        config = self._load_config()
        if clean_name not in config.get("agent", {}):
            raise KeyError(f"Agent '@{clean_name}' tidak ditemukan.")

        del config["agent"][clean_name]
        self._save_config(config)

        agent_file = self.agents_dir / f"{clean_name}.md"
        if agent_file.exists():
            shutil.move(str(agent_file), str(self.archive_dir / f"{clean_name}.md"))

        self.state_db.log_agent_event(
            action="RETIRE_AGENT",
            agent_name=clean_name,
            details="Agent dipensiunkan dan SOP diarsipkan ke .opencode/agent/archive/",
        )
        self.regenerate_org_chart()

    def regenerate_org_chart(self) -> Path:
        """
        Membuat ulang `docs/architecture/org-chart.md` secara otomatis agar CTO
        selalu bisa melihat bagan organisasi AI terbaru di Live Architecture Portal.
        """
        agents = self.list_agents()
        mermaid_lines = [
            "```mermaid",
            "flowchart TD",
            '    CTO["👨‍💻 CTO (Manusia - Pengambil Keputusan Tertinggi)"]',
            '    EM["👔 @em (Engineering Manager & Org Builder)"]',
            '    CTO -->|"Mandat & !approve"| EM',
        ]

        table_rows = [
            "| Agent ID | Role & Deskripsi | Status | Tools Diizinkan | File SOP |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for ag in agents:
            name = ag["name"]
            desc = ag["description"].replace('"', "'")
            tools_str = ", ".join(f"`{t}`" for t in ag["tools"]) or "*Read-Only*"
            badge = "Inti (Core)" if ag["is_core"] else "Spesialis Dinamis"
            table_rows.append(
                f"| `@{name}` | {desc} | **{badge}** | {tools_str} | `{ag['prompt_file']}` |"
            )

            if name == "em":
                continue
            node_id = f"AG_{name.replace('-', '_')}"
            short_title = desc.split(" - ")[0] if " - " in desc else desc[:38]
            mermaid_lines.append(f'    {node_id}["@{name}\\n({short_title})"]')
            if ag["is_core"]:
                mermaid_lines.append(f'    EM -->|"Delegasi SOP"| {node_id}')
            else:
                mermaid_lines.append(f'    EM -.->|"Dynamic Specialist"| {node_id}')

        mermaid_lines.append("```")

        events = self.state_db.get_recent_agent_events(limit=8)
        event_lines = []
        if events:
            for ev in events:
                event_lines.append(
                    f"- **`{ev['action']}`** pada `@{ev['agent_name']}`: `{ev['details']}`"
                )
        else:
            event_lines.append("- *Belum ada perubahan dinamis; struktur tim awal aktif.*")

        content = (
            "# Struktur Organisasi Karyawan AI (Live Org-Chart)\n\n"
            "> Halaman ini di-*generate* secara otomatis oleh **Dynamic Agent Factory (`orchestrator/agent_factory.py`)** setiap kali `@em` (Engineering Manager) atau **CTO** menambah agen baru (`hire_agent`) atau mengubah fokus agen (`specialize_agent`).\n\n"
            "## 1. Bagan Hirarki Tim Aktif\n\n"
            + "\n".join(mermaid_lines)
            + "\n\n## 2. Daftar Roster Karyawan AI & Izin Tool\n\n"
            + "\n".join(table_rows)
            + "\n\n## 3. Riwayat Evolusi Organisasi (HR & Specialization Log)\n\n"
            + "\n".join(event_lines)
            + "\n"
        )

        self.org_chart_path.write_text(content, encoding="utf-8")
        return self.org_chart_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamic Agent Factory CLI (HR & Org Builder)")
    sub = parser.add_subparsers(dest="command", required=True)

    hire_p = sub.add_parser("hire", help="Rekrut / buat agent spesialis baru")
    hire_p.add_argument("--name", required=True, help="ID agent (contoh: security-auditor)")
    hire_p.add_argument("--title", required=True, help="Jabatan agent")
    hire_p.add_argument("--description", required=True, help="Deskripsi singkat tugas")
    hire_p.add_argument("--sop", required=True, help="Aturan SOP spesifik")
    hire_p.add_argument("--tools", default="bash", help="Daftar tool dipisah koma (write,edit,bash)")

    spec_p = sub.add_parser("specialize", help="Pertajam / ubah fokus tugas agent")
    spec_p.add_argument("--name", required=True, help="ID agent yang ingin dipertajam")
    spec_p.add_argument("--focus", required=True, help="Instruksi spesialisasi baru")
    spec_p.add_argument("--replace", action="store_true", help="Ganti spesialisasi sebelumnya")

    retire_p = sub.add_parser("retire", help="Pensiunkan agent spesialis sementara")
    retire_p.add_argument("--name", required=True, help="ID agent spesialis")

    sub.add_parser("list", help="Tampilkan semua agent aktif")

    args = parser.parse_args()
    factory = AgentFactory()

    if args.command == "hire":
        tools_list = [t.strip() for t in args.tools.split(",") if t.strip()]
        res = factory.hire_agent(
            name=args.name,
            title=args.title,
            description=args.description,
            sop_rules=args.sop,
            allowed_tools=tools_list,
        )
        print(json.dumps({"status": "hired", "agent": args.name, "config": res}, indent=2))
    elif args.command == "specialize":
        res = factory.specialize_agent(
            name=args.name,
            additional_focus=args.focus,
            replace_specialization=args.replace,
        )
        print(json.dumps({"status": "specialized", "result": res}, indent=2))
    elif args.command == "retire":
        factory.retire_agent(args.name)
        print(json.dumps({"status": "retired", "agent": args.name}, indent=2))
    elif args.command == "list":
        print(json.dumps(factory.list_agents(), indent=2))


if __name__ == "__main__":
    main()
