# kcad-auto-pcb 进度记录

## 项目目录结构

```
d:\project\PCB Design\
├── src/kcad_auto_pcb/
│   ├── agent/
│   │   ├── agent.py            # PCBAgent 主逻辑
│   │   ├── tools.py            # Agent 工具集
│   │   └── domain_knowledge.py # 领域知识：封装→板子尺寸推断（公式驱动）
│   ├── cli/
│   │   └── main.py             # CLI 入口 + Web 启动
│   ├── config/
│   │   └── settings.py         # AppSettings + KiCad 自动检测
│   ├── engines/
│   │   ├── kicad_native.py     # pcbnew API 封装（KiCad 10.0 适配）
│   │   ├── freerouting_mgr.py  # FreeRouting subprocess + DSN/SES
│   │   └── kicad_bridge.py     # 子进程桥接（系统Python→KiCad Python）
│   ├── footprint/
│   │   ├── parser.py           # 封装库路径搜索 + 模糊匹配
│   │   └── cache.py            # 封装缓存
│   ├── llm/
│   │   ├── base.py             # LLM 抽象基类 + BackendFactory
│   │   └── providers/
│   │       ├── openai.py       # OpenAI/DashScope 适配
│   │       ├── deepseek.py     # DeepSeek 适配
│   │       ├── anthropic.py    # Anthropic 适配
│   │       └── ollama.py       # Ollama 本地模型
│   ├── pcb/
│   │   ├── board_builder.py    # PCBBoard 数据类构建
│   │   ├── exporter.py         # pcbnew API 导出 + JSON fallback
│   │   └── stackup.py          # 层叠定义
│   ├── pipeline/
│   │   ├── orchestrator.py     # 管线主调度（解析→布局→布线→导出）
│   │   ├── context.py          # PipelineContext
│   │   └── step.py
│   ├── placement/
│   │   ├── force_directed.py   # 布局算法（原理图映射+网格+双面平衡）
│   │   └── legalizer.py        # 重叠消除（层感知）
│   ├── routing/
│   │   ├── astar.py            # A* fallback 路由
│   │   ├── multi_layer.py      # 多层路由
│   │   └── via_placer.py
│   ├── schematic/
│   │   ├── parser.py           # .kicad_sch 解析 + PWR 过滤
│   │   ├── pdf_parser.py       # PDF 文本提取 + 元件/网表识别
│   │   ├── model.py            # Design/Component/Net 数据模型
│   │   └── connectivity.py     # 连通图
│   └── web/
│       ├── server.py           # FastAPI + WebSocket + REST
│       └── static/
│           └── index.html      # WebSocket 聊天前端
├── examples/
│   ├── ne555_astable.kicad_sch
│   └── simple_led.kicad_sch
├── monocle-schematics.pdf      # Monocle AR 原理图（仅PDF）
├── freerouting-1.9.0.jar       # FreeRouting 引擎
├── output/
├── tests/
└── PROGRESS.md
```

## 架构总览

```
用户输入（自然语言/文件）
    ↓
WebSocket 聊天 Agent（server.py: /ws/chat）
    ↓
领域知识推断（domain_knowledge.py）→ 板子尺寸、层数
    ↓
PipelineOrchestrator（orchestrator.py）
  ├── Stage 1: 解析原理图（parser.py / pdf_parser.py）
  ├── Stage 2: 封装解析（footprint/）
  ├── Stage 3: 布局（force_directed.py + legalizer.py）
  ├── Stage 4: 布线（FreeRouting via kicad_bridge.py）
  └── Stage 5: 导出（exporter.py → pcbnew API）
    ↓
.kicad_pcb 文件 + SVG 预览
```

## 关键数据

### NE555（.kicad_sch，8元件）
- 布局：力导向
- 板子：~60×55mm
- 布线：FreeRouting 全部完成
- 状态：✅ 完全可用

### Monocle（PDF，168元件）
- 布局：双面网格，88×86mm
- 封装：119×0603 + 20×0805 + 29×大封装
- 网表：空间邻近推导（474走线，14过孔）
- 状态：⚠️ 网表缺失（PDF无连线信息）

## 功能清单

| 功能 | 状态 | 说明 |
|------|------|------|
| .kicad_sch 解析 | ✅ | 含 PWR 过滤 |
| PDF 文本解析 | ✅ | 元件+网表名提取 |
| PDF 网表推导 | ⚠️ | 空间邻近法，非精确 |
| 力导向布局 | ✅ | 小设计<30元件 |
| 双面网格布局 | ✅ | 大设计双面平衡 |
| 自动板子尺寸 | ✅ | 封装面积公式计算 |
| FreeRouting | ✅ | JAR 子进程，双层/四层 |
| pcbnew API 导出 | ✅ | KiCad 10.0 适配 |
| WebSocket 聊天 | ✅ | 实时对话+文件上传 |
| 领域知识推断 | ✅ | 公式驱动，不限场景 |
| LLM 优化 | ✅ | DashScope qwen3.6-plus |
| 会话持久化 | ✅ | JSON 文件 |
| SVG PCB 预览 | ✅ | 从 .kicad_pcb 文件渲染 |
| DRC 自愈循环 | ❌ | 未实现 |
| 增量修改 | ❌ | 未实现 |
| Gerber/BOM 导出 | ❌ | 未实现 |
| 多模态 PDF 看图 | ❌ | 需要视觉 LLM |

## 环境依赖

- Python 3.10+（系统）+ pydantic-settings, networkx
- KiCad 10.0.1（C:\Program Files\KiCad\10.0\）
- Java 17+（FreeRouting JAR）
- DashScope API key（可选，LLM 增强）
