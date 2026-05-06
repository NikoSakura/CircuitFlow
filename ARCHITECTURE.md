# kcad-auto-pcb 技术架构

## 六层结构

```
┌──────────────────────────────────────────────────────┐
│  Layer 5: Web UI (FastAPI + 原生 HTML/JS)            │
│  http://127.0.0.1:7860                               │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐          │
│  │ 模型供应商    │ │ 一键设计  │ │ AI 智能体│          │
│  │ (AstrBot风格)│ │          │ │          │          │
│  └─────────────┘ └──────────┘ └──────────┘          │
├──────────────────────────────────────────────────────┤
│  Layer 4: Agent 编排层 (tools/agent_core.py)         │
│                                                      │
│  run_pcb_design() 闭环:                              │
│  parse → plan → route → DRC → fix → repeat → deliver│
│                                                      │
│  ┌───────────────┐  ┌─────────────────┐              │
│  │ prompt.py     │  │ screenshot.py   │              │
│  │ System Prompt │  │ 视觉检查+描述    │              │
│  │ Skills ×6    │  │ (PNG/SVG/Text)  │              │
│  │ Subagent      │  │                 │              │
│  └───────────────┘  └─────────────────┘              │
├──────────────────────────────────────────────────────┤
│  Layer 3: 工具层 (tools/)                            │
│                                                      │
│  smart_parse.py    plan_routing()    run_drc()       │
│  原理图→物理spec     L形曼哈顿走线    DRC检查         │
│                                                      │
│  converter.py      draw.py           spec.py         │
│  Spec→.kicad_pcb   绘图工具          Spec数据结构     │
├──────────────────────────────────────────────────────┤
│  Layer 2: 核心算法层                                  │
│                                                      │
│  placement/     routing/        pcb/                 │
│  网格布局        A* 寻路(备用)   board_builder        │
│  force_directed                 exporter (.kicad_pcb) │
│  legalizer                      stackup (2-4层)      │
│                                                      │
│  geometry/      footprint/      llm/                 │
│  Point/Rect     封装库+生成器    多供应商 + Token预算  │
│  Grid/Transform 参数化封装       providers ×18        │
├──────────────────────────────────────────────────────┤
│  Layer 1: 数据层                                      │
│                                                      │
│  knowledge/pcb_rules.yaml    data/providers.py       │
│  设计规则数据库               供应商持久化             │
│  ┌─────────────────────────────────────┐             │
│  │ PWR_TRACE_WIDTH: 0.5mm             │             │
│  │ SIGNAL_TRACE_WIDTH: 0.25mm         │             │
│  │ COMPONENT_SPACING: 2mm             │             │
│  │ BOARD_EDGE_CLEARANCE: 0.5mm        │             │
│  └─────────────────────────────────────┘             │
├──────────────────────────────────────────────────────┤
│  Layer 0: 输入层                                      │
│                                                      │
│  schematic/parser.py    schematic/pdf_parser.py      │
│  .kicad_sch 解析       PDF文本提取+LLM多模态识别      │
│  (KiCad S-expression)  (PyMuPDF + LLM Vision)        │
└──────────────────────────────────────────────────────┘
```

## 核心数据流

```
.kicad_sch / PDF
      │
      ▼
[smart_parse] ──→ PhysicalSpec {
                     board: BoardSize,
                     footprint_map: {ref→封装名},
                     pad_positions: {ref→[(x,y)]},
                     nets: [{name, pins}]
                   }
      │
      ▼
[load_rules] ──→ pcb_rules.yaml {
                   电源线宽: 0.5mm
                   信号线宽: 0.25mm
                   间距: 0.15mm
                 }
      │
      ▼
[plan_routing] ──→ BoardSpec {
                     components: [grid布局坐标],
                     traces: [L形曼哈顿路径]
                   }
      │
      ▼  ←────────── fix ──────────┐
[run_autoroute] → .kicad_pcb      │
      │                           │
      ▼                           │
[run_drc] → errors? ──Yes────────┘
      │
      No (0 errors)
      ▼
[Deliver] → .kicad_pcb + .json + SVG 预览
```

## Agent 架构（设计模式）

```
用户上传原理图
      │
      ▼
┌─────────────────────────────────────┐
│ Agent Core (agent_core.py)          │
│                                     │
│  System Prompt 定义的约束:           │
│  ✅ 保护已有器件 - 只改走线          │
│  ✅ 增量更新 - 不重新生成            │
│  ✅ 作用域隔离 - 只修出错的net       │
│                                     │
│  布线前视觉检查:                     │
│  screenshot → 分析障碍 → 规划路径    │
│         → 汇报 → 确认 → 执行        │
│                                     │
│  Subagent: 多页设计分治              │
│  每页独立 → 接口对齐 → 合并          │
└─────────────────────────────────────┘
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/spec-design` | Agent 闭环设计（主入口） |
| POST | `/api/design` | 传统 Pipeline 设计 |
| POST | `/api/agent` | AI 智能体模式 |
| POST | `/api/analyze` | 分析原理图 |
| GET  | `/api/download/{sid}/{type}` | 下载生成文件 |
| GET  | `/api/templates` | 供应商模板列表 |
| GET  | `/api/sources` | 已配置供应商 |
| POST | `/api/sources` | 添加供应商 |
| PUT  | `/api/sources/{id}` | 更新供应商 |
| DELETE | `/api/sources/{id}` | 删除供应商 |
| GET  | `/api/sources/{id}/models` | 供应商模型列表 |
| POST | `/api/sources/{id}/test` | 测试供应商连接 |
| POST | `/api/providers/fetch-models` | 自动获取模型列表 |
| GET  | `/api/history` | 历史记录 |

## Skills（6个可复用能力）

| Skill | 说明 | 规则 |
|-------|------|------|
| `parse_and_plan` | 解析原理图 + 规划布局 | 网格排列，IC居中 |
| `route_power_nets` | 电源网络优先布线 | 0.5mm宽，最短路径 |
| `route_signal_nets` | 信号网络布线 | 0.25mm宽，L形曼哈顿 |
| `place_decoupling` | 去耦电容靠近IC | 距离≤5mm |
| `run_and_fix_drc` | DRC检查+修正循环 | 最大3轮迭代 |
| `add_ground_plane` | 接地覆铜 | 底层GND填充 |

## 设计规则（pcb_rules.yaml）

| 规则ID | 类别 | 约束 | 优先级 |
|--------|------|------|--------|
| PWR_TRACE_WIDTH | routing | width ≥ 0.5mm | CRITICAL |
| SIGNAL_TRACE_WIDTH | routing | width ≥ 0.25mm | HIGH |
| SIGNAL_SPACING | clearance | spacing ≥ 0.15mm | HIGH |
| BOARD_EDGE_CLEARANCE | placement | edge ≥ 0.5mm | HIGH |
| COMPONENT_SPACING | placement | gap ≥ 2.0mm | HIGH |
| VIA_DRILL_MIN | via | drill ≥ 0.3mm | - |
| TRACE_TO_TRACE | clearance | spacing ≥ 0.15mm | HIGH |
| TRACE_TO_PAD | clearance | spacing ≥ 0.15mm | HIGH |
| DECOUPLING_PROXIMITY | placement | cap-to-IC ≤ 5mm | MEDIUM |

## 供应商系统

18个LLM供应商，AstrBot同款LobeHub图标，支持多同类型实例。

| 供应商 | 类型 | 多模态 | 自动获取模型 |
|--------|------|--------|-------------|
| OpenAI | openai | ✅ | ✅ GET /v1/models |
| Anthropic | anthropic | ✅ | ❌ |
| DeepSeek | deepseek | ❌ | ✅ |
| Ollama | ollama | ✅ | ✅ GET /api/tags |
| 阿里云百炼 | openai_compatible | ✅ | ✅ |
| 智谱GLM | openai_compatible | ✅ | ❌ |
| SiliconFlow | openai_compatible | ✅ | ✅ |
| Moonshot | openai_compatible | ❌ | ✅ |
| ModelScope | openai_compatible | ❌ | ✅ |
| 火山引擎 | openai_compatible | ❌ | ✅ |
| xAI Grok | openai_compatible | ✅ | ✅ |
| Groq | openai_compatible | ❌ | ✅ |
| OpenRouter | openai_compatible | ✅ | ✅ |
| NVIDIA NIM | openai_compatible | ❌ | ✅ |
| vLLM | openai_compatible | ❌ | ✅ |
| LM Studio | openai_compatible | ❌ | ✅ |
| 通用OpenAI兼容 | openai_compatible | 视情况 | ✅ |

## 关键数字

| 指标 | 数值 |
|------|------|
| Python源文件 | 59个 |
| 测试 | 72个全通过 |
| 供应商 | 18类 |
| 内置封装 | 22种 |
| 设计规则 | 9条 |
| Skills | 6个 |
| API端点 | 14个 |
| NE555生成 | ~2秒, DRC 0错误 |
