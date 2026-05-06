# CircuitFlow

> AI-assisted PCB design — parse, place, route, export. One click from schematic to manufacturable board.

```
原理图 ─→ 解析 ─→ 布局 ─→ 布线 ─→ .kicad_pcb
   ↑                        ↑
   └── Agent 对话 + LLM ────┘
```

## Quick Start

```bash
pip install -r requirements.txt
python -m kcad_auto_pcb.cli.main web
# Open http://127.0.0.1:7860
```

## Features

- **Multi-format input**: `.kicad_sch` native parsing + PDF schematic extraction
- **Auto placement**: schematic-aware hierarchical layout, double-sided, auto board sizing
- **Auto routing**: FreeRouting engine (Java) via subprocess bridge, 2-8 layer support
- **pcbnew API export**: no string concatenation — all output via KiCad's official Python API
- **WebSocket agent**: real-time chat with domain knowledge (scene-aware board sizing)
- **17 LLM templates**: OpenAI, DeepSeek, DashScope, Ollama, SiliconFlow, and more

## Architecture

| Layer | Module | Role |
|-------|--------|------|
| Input | `schematic/` | Parse .kicad_sch / PDF, extract components + nets |
| Engines | `engines/` | KiCad pcbnew API wrapper, FreeRouting subprocess |
| Placement | `placement/` | Force-directed, hierarchical block, double-sided grid |
| Routing | `routing/` | A* fallback, via placer, multi-layer support |
| Export | `pcb/` | Board builder → pcbnew.SaveBoard |
| Agent | `agent/` | WebSocket chat, domain knowledge, LLM orchestration |
| Web | `web/` | FastAPI + WebSocket + chat UI |

## Requirements

- Python 3.10+
- [KiCad 10.0+](https://www.kicad.org/) (auto-detected)
- Java 17+ (for FreeRouting)
- Optional: LLM API key for AI optimization

## License

MIT
