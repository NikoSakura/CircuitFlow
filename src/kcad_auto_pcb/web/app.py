"""kcad-auto-pcb Web UI — AstrBot-style provider management."""

from __future__ import annotations
import asyncio, json, tempfile
from pathlib import Path
import gradio as gr

from ..config.settings import AppSettings
from ..pipeline.orchestrator import PipelineOrchestrator
from ..schematic.parser import SchematicParser
from ..footprint.parser import FootprintParser
from ..data.providers import ProviderManager, PROVIDER_TEMPLATES, ProviderConfig
from .preview import render_pcb_svg

_provider_mgr = ProviderManager()

CN_KEYS = {
    "components": "元器件", "traces": "布线", "vias": "过孔",
    "nets": "网表", "total_trace_length_mm": "总线长(mm)",
    "layers": "层数", "board_size": "板尺寸",
}

# ── AstrBot-style provider type cards (HTML) ──────────────────────────

PROVIDER_CARD_CSS = """
<style>
.pcard {
  border:1px solid #ddd; border-radius:8px; padding:12px 16px;
  margin:6px 0; cursor:pointer; display:flex; align-items:center;
  gap:12px; transition:all .2s; background:#fff;
}
.pcard:hover { border-color:#667eea; box-shadow:0 2px 8px rgba(102,126,234,.2); }
.pcard.active { border-color:#667eea; background:#f0f2ff; border-width:2px; }
.pcard img { width:32px; height:32px; }
.pcard .info { flex:1; }
.pcard .info .name { font-weight:600; font-size:15px; }
.pcard .info .desc { font-size:12px; color:#666; margin-top:2px; }
.pcard .tags { display:flex; gap:4px; flex-wrap:wrap; }
.pcard .tag {
  font-size:10px; padding:2px 6px; border-radius:10px;
  background:#e8e8e8; color:#555;
}
.pcard .tag.good { background:#e6f9e6; color:#2e7d32; }
.pcard .tag.warn { background:#fff3e0; color:#e65100; }

.prov-list { max-height:400px; overflow-y:auto; }
.prov-item {
  border:1px solid #e0e0e0; border-radius:6px; padding:10px 14px;
  margin:6px 0; display:flex; align-items:center; gap:10px;
  background:#fafafa;
}
.prov-item .actions { display:flex; gap:6px; }
.prov-item .actions button {
  font-size:11px; padding:3px 8px; border:1px solid #ccc;
  border-radius:4px; cursor:pointer; background:#fff;
}
.prov-item .actions button.danger { color:#d32f2f; border-color:#d32f2f; }
.prov-item .actions button.primary { color:#1976d2; border-color:#1976d2; }
.prov-item .dot { width:8px; height:8px; border-radius:50%; }
.prov-item .dot.on { background:#4caf50; }
.prov-item .dot.off { background:#bdbdbd; }

.section-title { font-size:16px; font-weight:600; margin:16px 0 8px 0; border-bottom:2px solid #667eea; padding-bottom:4px; }
</style>
"""


def _render_type_cards(active_type: str = "openai") -> str:
    """Render provider type selection cards with SVG icons — like AstrBot's provider source selector."""
    templates = _provider_mgr.list_templates()
    cards = []
    for t in templates:
        active_cls = " active" if t["type"] == active_type else ""
        icon_html = f'<img src="{t["icon"]}" onerror="this.style.display=\'none\'">' if t.get("icon") else ""
        tags = []
        if t["supports_vision"]:
            tags.append('<span class="tag good">👁 多模态</span>')
        if t.get("supports_model_fetch"):
            tags.append('<span class="tag good">🔍 可自动获取模型</span>')
        if t["requires_key"]:
            tags.append('<span class="tag warn">🔑 需要密钥</span>')
        else:
            tags.append('<span class="tag good">🆓 无需密钥</span>')

        cards.append(f'''
        <div class="pcard{active_cls}" data-type="{t['type']}">
          {icon_html}
          <div class="info">
            <div class="name">{t['name']}</div>
            <div class="desc">{t['description']}</div>
          </div>
          <div class="tags">{"".join(tags)}</div>
        </div>''')

    return PROVIDER_CARD_CSS + f'''
    <div class="section-title">选择供应商类型</div>
    <div class="type-cards">{"".join(cards)}</div>
    '''


def _render_configured_providers() -> str:
    """Render configured providers as a styled list — like AstrBot's provider list."""
    providers = _provider_mgr.list_all()
    if not providers:
        return '<div style="color:#999;padding:20px;text-align:center">暂无已配置的供应商</div>'

    active_id = _provider_mgr._store.active_provider_id
    items = []
    for p in providers:
        tmpl = PROVIDER_TEMPLATES.get(p.type, {})
        icon_html = f'<img src="{tmpl.get("icon", "")}" width="20" height="20" onerror="this.style.display=\'none\'">'
        dot_cls = "on" if p.enable else "off"
        is_active = p.id == active_id

        items.append(f'''
        <div class="prov-item">
          <div class="dot {dot_cls}"></div>
          {icon_html}
          <div style="flex:1">
            <div style="font-weight:600">{p.name} {"⭐" if is_active else ""}</div>
            <div style="font-size:11px;color:#888">{p.type}:{p.default_model or "?"}</div>
          </div>
        </div>''')

    return f'''
    <div class="section-title">已配置的供应商 ({len(providers)})</div>
    <div class="prov-list">{"".join(items)}</div>
    '''


# ── Handlers ─────────────────────────────────────────────────────────

def _reload():
    """刷新供应商 UI."""
    providers = _provider_mgr.list_all()
    if not providers:
        choices = [("请先添加供应商", "")]
    else:
        choices = [(f"{p.name} | {p.type}:{p.default_model or '?'}", p.id) for p in providers]
    active = _provider_mgr._store.active_provider_id
    return (
        gr.update(choices=choices, value=active),
        _render_configured_providers(),
        _render_type_cards(),
    )


def add_provider(ptype, name, key, url, model):
    if not name.strip():
        return *_reload(), "**请填写供应商名称**"
    tmpl = PROVIDER_TEMPLATES.get(ptype, {})
    if tmpl.get("requires_key") and not key.strip():
        return *_reload(), f"**{tmpl['name']} 需要 API 密钥**"
    models = [m.strip() for m in model.split(",") if m.strip()] if model.strip() else list(tmpl.get("models", []))
    p = ProviderConfig.from_template(ptype, name.strip(), key.strip(), url.strip() or tmpl.get("base_url", ""))
    p.models = models
    p.default_model = models[0] if models else tmpl.get("default_model", "")
    _provider_mgr.add(p)
    return *_reload(), f"✅ 已添加: **{p.name}**"


def delete_provider(pid):
    if not pid:
        return *_reload(), "请先选择要删除的供应商"
    p = _provider_mgr.get(pid)
    _provider_mgr.delete(pid)
    return *_reload(), f"已删除: {p.name if p else pid}"


def set_active(pid):
    if not pid:
        return *_reload(), "请先选择供应商"
    _provider_mgr.set_active(pid)
    p = _provider_mgr.get(pid)
    return *_reload(), f"⭐ 当前供应商: **{p.name if p else '?'}**"


def test_provider(pid):
    if not pid:
        return "请先选择供应商"
    p = _provider_mgr.get(pid)
    if not p:
        return "供应商不存在"
    try:
        from ..llm.base import LLMBackendFactory
        spec = p.full_spec
        kwargs = {}
        if p.type in ("openai", "openai_compatible"):
            kwargs["api_key"] = p.api_key
            if p.base_url: kwargs["base_url"] = p.base_url
        elif p.type == "anthropic":
            kwargs["api_key"] = p.api_key
        elif p.type == "deepseek":
            kwargs["api_key"] = p.api_key
        elif p.type == "ollama":
            kwargs["base_url"] = p.base_url
        backend = LLMBackendFactory.create(spec, **{k: v for k, v in kwargs.items() if v})
        tokens = backend.token_count("test")
        return f"✅ 连接成功! {p.name} | {p.type}:{p.default_model} | ~{tokens} tokens"
    except Exception as e:
        return f"❌ 连接失败: {str(e)[:200]}"


def on_type_selected(ptype):
    """当供应商类型改变时更新卡片和默认 URL."""
    tmpl = _provider_mgr.get_template(ptype)
    if not tmpl:
        return _render_type_cards(ptype), "", ""
    api_doc = tmpl.get("api_doc", "")
    fetch = "✅ 支持" if tmpl.get("supports_model_fetch") else "❌ 需手动输入"
    info = f"📖 [获取 Key]({api_doc}) | 自动获取模型: {fetch}" if api_doc else f"自动获取模型: {fetch}"
    return _render_type_cards(ptype), tmpl.get("base_url", ""), info


def fetch_models(ptype, key, url):
    if not key.strip() and ptype != "ollama":
        return "**请先填写 API 密钥**", gr.update()
    models = _provider_mgr.fetch_models(ptype, key.strip(), url.strip())
    if not models:
        return "**未获取到模型列表**，请检查密钥和地址", gr.update()
    if models[0].startswith("__error__:"):
        return f"**获取失败**: {models[0][10:]}", gr.update()
    return f"✅ 获取到 {len(models)} 个模型", gr.update(value=", ".join(models[:25]))


def get_provider_choices():
    providers = _provider_mgr.list_all()
    if not providers:
        return gr.update(choices=[("请先在「模型管理」添加", "")], value="")
    choices = [(f"{p.name} | {p.type}:{p.default_model or '?'}", p.id) for p in providers]
    return gr.update(choices=choices, value=_provider_mgr._store.active_provider_id)


def analyze_schematic(f):
    if f is None: return "未上传文件"
    ft = _file_type(f)
    if ft == "image": return "**PDF/图片** — 勾选「启用 AI」并选择多模态供应商后直接生成"
    if ft == "unknown": return "**格式不支持** — 请上传 .kicad_sch / .pdf / .png"
    parser = SchematicParser()
    design = parser.parse(str(f.name))
    fp_parser = FootprintParser()
    lines = [f"## 分析结果\n**元器件**: {design.component_count} | **网表**: {design.net_count}\n"]
    for ref, comp in sorted(design.components.items()):
        ok = "✓" if fp_parser.resolve(comp.footprint_name) else "⚠"
        lines.append(f"- **{ref}**: {comp.value} | {comp.footprint_name or '?'} {ok}")
    lines.append("")
    for name, net in sorted(design.nets.items()):
        pw = "⚡" if net.is_power else "·"
        pins = ", ".join(f"{p.component_ref}.{p.pin_number}" for p in net.pins[:6])
        lines.append(f"- {pw} **{name}**: {pins}")
    return "\n".join(lines)


def _file_type(f):
    if f is None: return "none"
    n = f.name if hasattr(f, 'name') else str(f)
    s = Path(n).suffix.lower()
    if s == ".kicad_sch": return "kicad"
    if s in (".pdf",".png",".jpg",".jpeg"): return "image"
    return "unknown"


def _make_settings(p: ProviderConfig) -> AppSettings:
    s = AppSettings()
    spec = p.full_spec
    if p.type in ("openai", "openai_compatible"):
        s.openai_api_key = p.api_key; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    elif p.type == "anthropic":
        s.anthropic_api_key = p.api_key; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    elif p.type == "deepseek":
        s.deepseek_api_key = p.api_key; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    elif p.type == "ollama":
        s.ollama_base_url = p.base_url; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    return s


def run_pipeline(f, layers, bw, bh, enable_ai, provider_id):
    if f is None: return render_pcb_svg(None), "请上传原理图", None, None
    ft = _file_type(f); fpath = str(f.name); pre = None
    settings = AppSettings()
    if enable_ai and provider_id:
        p = _provider_mgr.get(provider_id)
        if p: settings = _make_settings(p)

    if ft == "image":
        if not (enable_ai and provider_id):
            return render_pcb_svg(None), "**PDF/图片需要多模态 LLM**\n勾选「启用 AI」+ 选择供应商（Anthropic/GPT-4o）", None, None
        p = _provider_mgr.get(provider_id)
        if not p: return render_pcb_svg(None), "供应商无效", None, None
        tmpl = PROVIDER_TEMPLATES.get(p.type, {})
        if not tmpl.get("supports_vision"):
            return render_pcb_svg(None), f"{p.type} 不支持图片识别", None, None
        try:
            from ..schematic.multimodal_reader import MultimodalSchematicReader
            from ..llm.base import LLMBackendFactory
            kwargs = {}
            if p.type in ("openai","openai_compatible"): kwargs["api_key"] = p.api_key
            elif p.type == "anthropic": kwargs["api_key"] = p.api_key
            elif p.type == "deepseek": kwargs["api_key"] = p.api_key
            elif p.type == "ollama": kwargs["base_url"] = p.base_url
            backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
            reader = MultimodalSchematicReader(backend)
            pre = asyncio.run(reader.read(fpath))
        except Exception as e:
            return render_pcb_svg(None), f"**AI 识别失败**: {e}", None, None
    elif ft == "unknown":
        return render_pcb_svg(None), "**格式不支持**", None, None

    with tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False) as tmp:
        out = tmp.name
    orch = PipelineOrchestrator(settings)
    ctx = asyncio.run(orch.run(
        schematic_path=fpath, output_path=out, board_layers=layers,
        board_width=bw if bw > 0 else None, board_height=bh if bh > 0 else None,
        enable_llm_placement=enable_ai, enable_llm_routing=enable_ai,
        pre_parsed_design=pre,
    ))
    svg = render_pcb_svg(ctx.board) if ctx.board else render_pcb_svg(None)
    parts = []
    for k, v in ctx.stats.get("summary", {}).items():
        label = CN_KEYS.get(k, k)
        parts.append(f"**{label}**: {v:.1f}" if isinstance(v, float) else f"**{label}**: {v}")
    if ctx.errors: parts.append("\n**错误**: " + "; ".join(ctx.errors))
    jp = out.replace(".kicad_pcb", ".json")
    json.dump({"summary": ctx.stats.get("summary",{}), "errors": ctx.errors}, open(jp,"w"), indent=2, ensure_ascii=False)
    return svg, "\n".join(parts), out, jp


def run_agent(f, layers, provider_id, max_iters):
    if f is None: return render_pcb_svg(None), "请上传原理图", None, None, "[]"
    ft = _file_type(f); fpath = str(f.name); pre = None
    settings = AppSettings()
    if provider_id:
        p = _provider_mgr.get(provider_id)
        if p: settings = _make_settings(p)
    if ft == "image":
        if not provider_id: return render_pcb_svg(None), "PDF/图片需要多模态供应商", None, None, "[]"
        p = _provider_mgr.get(provider_id)
        if not p: return render_pcb_svg(None), "供应商无效", None, None, "[]"
        try:
            from ..schematic.multimodal_reader import MultimodalSchematicReader
            from ..llm.base import LLMBackendFactory
            kwargs = {}
            if p.type in ("openai","openai_compatible"): kwargs["api_key"] = p.api_key
            elif p.type == "anthropic": kwargs["api_key"] = p.api_key
            elif p.type == "deepseek": kwargs["api_key"] = p.api_key
            elif p.type == "ollama": kwargs["base_url"] = p.base_url
            backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
            reader = MultimodalSchematicReader(backend)
            pre = asyncio.run(reader.read(fpath))
        except Exception as e:
            return render_pcb_svg(None), f"AI 识别失败: {e}", None, None, "[]"
    elif ft == "unknown":
        return render_pcb_svg(None), "格式不支持", None, None, "[]"

    from ..agent.agent import PCBAgent
    agent = PCBAgent(settings); agent.MAX_ITERATIONS = max_iters
    with tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False) as tmp:
        out = tmp.name
    result = asyncio.run(agent.design(
        schematic_path=fpath, output_path=out, board_layers=layers, pre_parsed_design=pre,
    ))
    svg = render_pcb_svg(None)
    summary = f"**状态**: {result.get('status','?')} | **迭代**: {result.get('iterations',0)} | **动作**: {result.get('actions',0)}"
    history = json.dumps(result.get("history",[]), indent=2, ensure_ascii=False)
    return svg, summary, out, out.replace(".kicad_pcb",".json"), history


# ── UI ───────────────────────────────────────────────────────────────

def _initial_prov_list():
    providers = _provider_mgr.list_all()
    if not providers:
        return [("请先添加供应商", "")], _render_configured_providers()
    choices = [(f"{p.name} | {p.type}:{p.default_model or '?'}", p.id) for p in providers]
    return choices, _render_configured_providers()


def create_ui():
    initial_choices, initial_prov_html = _initial_prov_list()

    with gr.Blocks(title="kcad-auto-pcb") as app:
        gr.HTML("""
        <div style="display:flex;align-items:center;gap:12px;padding:8px 0">
          <h1 style="margin:0">kcad-auto-pcb</h1>
          <span style="color:#888;font-size:13px">AI 自动 PCB 设计 — 上传原理图 → 生成可打样 PCB</span>
        </div>
        """)

        with gr.Tabs():
            # ═══════════ 模型管理 — AstrBot 风格 ═══════════
            with gr.Tab("模型管理"):
                # Provider type selector — 卡片式 Radio
                templates = _provider_mgr.list_templates()
                radio_choices = []
                for t in templates:
                    icon = {"openai":"🟢","anthropic":"🟣","deepseek":"🔵","ollama":"🦙","openai_compatible":"🔌"}.get(t["type"],"")
                    vision = "👁" if t["supports_vision"] else ""
                    fetch = "🔍" if t.get("supports_model_fetch") else ""
                    radio_choices.append((f"{icon} {t['name']}  {vision}{fetch}", t["type"]))

                type_radio = gr.Radio(radio_choices, value="openai", label="选择供应商类型")

                type_info = gr.HTML(_render_type_info("openai"))

                with gr.Row():
                    with gr.Column(scale=3):
                        add_name = gr.Textbox("", label="供应商名称", placeholder="例如: 我的 OpenAI")
                    with gr.Column(scale=3):
                        add_key = gr.Textbox("", label="API 密钥", type="password")

                with gr.Row():
                    with gr.Column(scale=4):
                        add_url = gr.Textbox(
                            _provider_mgr.get_template("openai").get("base_url", ""),
                            label="接口地址",
                        )
                    with gr.Column(scale=1):
                        btn_fetch = gr.Button("🔍 获取模型", size="sm")

                add_model = gr.Textbox("", label="模型列表（逗号分隔，留空用默认）")
                fetch_msg = gr.Markdown("")

                with gr.Row():
                    btn_add = gr.Button("➕ 添加供应商", variant="primary")
                    btn_test = gr.Button("🔗 测试连接")
                    add_msg = gr.Markdown("")

                # Provider info changes with type selection
                type_radio.change(
                    lambda t: (_render_type_info(t), _provider_mgr.get_template(t).get("base_url",""), _provider_mgr.get_template(t).get("api_doc","")),
                    [type_radio], [type_info, add_url, add_msg],
                )

                # ══ 已配置供应商 ══
                gr.Markdown("---")
                gr.Markdown("### 已配置的供应商")
                with gr.Row():
                    prov_html = gr.HTML(initial_prov_html)
                with gr.Row():
                    provider_dd = gr.Dropdown(
                        choices=initial_choices, label="选择供应商进行操作",
                        value=_provider_mgr._store.active_provider_id, scale=3,
                    )
                    btn_active = gr.Button("⭐ 设为当前", scale=1)
                    btn_del = gr.Button("🗑 删除", variant="stop", scale=1)
                op_msg = gr.Markdown("")

                # Wire events
                btn_add.click(add_provider, [type_radio, add_name, add_key, add_url, add_model],
                              [provider_dd, prov_html, type_info, add_msg])
                btn_active.click(set_active, [provider_dd], [provider_dd, prov_html, type_info, op_msg])
                btn_del.click(delete_provider, [provider_dd], [provider_dd, prov_html, type_info, op_msg])
                btn_test.click(test_provider, [provider_dd], [add_msg])
                btn_fetch.click(fetch_models, [type_radio, add_key, add_url], [fetch_msg, add_model])

            # ═══════════ 一键设计 ═══════════
            with gr.Tab("一键设计"):
                with gr.Row():
                    with gr.Column(scale=1):
                        schematic = gr.File(label="上传原理图", file_types=[".kicad_sch", ".pdf", ".png", ".jpg"])
                        btn_analyze = gr.Button("分析原理图")

                        layers = gr.Slider(2, 4, value=2, step=2, label="板层数")
                        bw = gr.Number(0, label="板宽 mm（0=自动）", precision=0)
                        bh = gr.Number(0, label="板高 mm（0=自动）", precision=0)

                        enable_llm = gr.Checkbox(False, label="启用 AI 优化布局布线")
                        design_prov = gr.Dropdown(
                            label="选择供应商", choices=[("未启用 AI", "")],
                        )
                        enable_llm.change(lambda v: get_provider_choices(), [enable_llm], [design_prov])
                        btn_gen = gr.Button("生成 PCB", variant="primary")

                    with gr.Column(scale=1):
                        analysis = gr.Markdown("上传原理图，点击「分析原理图」查看器件信息。")

                with gr.Row():
                    with gr.Column(scale=2):
                        preview = gr.HTML(render_pcb_svg(None), elem_classes=["pcb-preview"])
                    with gr.Column(scale=1):
                        summary = gr.Markdown("等待生成...")
                        out_kicad = gr.File(label="下载 .kicad_pcb")
                        out_json = gr.File(label="下载 .json")

                btn_analyze.click(analyze_schematic, [schematic], [analysis])
                btn_gen.click(run_pipeline, [schematic, layers, bw, bh, enable_llm, design_prov],
                              [preview, summary, out_kicad, out_json])

            # ═══════════ AI 智能体 ═══════════
            with gr.Tab("AI 智能体"):
                gr.Markdown("AI 自主迭代设计 — 需先在「模型管理」配置供应商。")
                with gr.Row():
                    with gr.Column(scale=1):
                        ag_f = gr.File(label="上传原理图", file_types=[".kicad_sch", ".pdf", ".png", ".jpg"])
                        ag_l = gr.Slider(2, 4, value=2, step=2, label="板层数")
                        ag_p = gr.Dropdown(label="选择供应商", choices=[("未配置", "")])
                        ag_l.change(lambda: get_provider_choices(), outputs=[ag_p])
                        ag_i = gr.Slider(5, 30, value=15, step=1, label="最大迭代次数")
                        btn_ag = gr.Button("启动 AI 设计", variant="primary")
                    with gr.Column(scale=1):
                        ag_preview = gr.HTML(render_pcb_svg(None), elem_classes=["pcb-preview"])
                with gr.Row():
                    ag_sum = gr.Markdown("**状态**: 等待...")
                    ag_k = gr.File(label="下载 .kicad_pcb")
                    ag_j = gr.File(label="下载 .json")
                    ag_h = gr.Code(label="执行历史", language="json")
                btn_ag.click(run_agent, [ag_f, ag_l, ag_p, ag_i],
                             [ag_preview, ag_sum, ag_k, ag_j, ag_h])

            # ═══════════ 使用说明 ═══════════
            with gr.Tab("使用说明"):
                gr.Markdown("""
                ## 快速上手
                1. **模型管理** → 选择供应商类型 → 填 API 密钥 → 添加 → 设为当前
                2. **一键设计** → 上传原理图 → 分析 → 生成 PCB
                3. **AI 智能体** → AI 自主迭代设计

                ### 供应商类型
                | 类型 | 多模态 | 自动获取模型 | 需要密钥 |
                |------|--------|-------------|---------|
                | 🟢 OpenAI | ✅ | ✅ /v1/models | 是 |
                | 🟣 Anthropic | ✅ | ❌ 手动输入 | 是 |
                | 🔵 DeepSeek | ❌ | ✅ /v1/models | 是 |
                | 🦙 Ollama | ✅ | ✅ /api/tags | 否 |
                | 🔌 OpenAI 兼容 | 视情况 | ✅ | 是 |

                ### 支持格式
                - `.kicad_sch` — KiCad 7/8 原理图，直接解析
                - `.pdf` / `.png` — 需要多模态 LLM（Anthropic Claude 或 GPT-4o）
                """)

    return app


def main():
    create_ui().launch(
        server_name="127.0.0.1", server_port=7860,
        css="""
        footer{display:none!important}
        .pcb-preview{background:#0a0a14;border:2px solid #333;border-radius:8px;padding:8px;min-height:320px}
        """
    )


if __name__ == "__main__":
    main()
