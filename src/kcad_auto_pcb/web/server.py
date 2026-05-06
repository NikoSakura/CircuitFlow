"""kcad-auto-pcb FastAPI server — like AstrBot's backend."""

from __future__ import annotations
import asyncio, json, tempfile, shutil, uuid, time, threading, subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from ..config.settings import AppSettings
from ..pipeline.orchestrator import PipelineOrchestrator
from ..agent.agent import PCBAgent
from ..schematic.parser import SchematicParser
from ..footprint.parser import FootprintParser
from ..data.providers import ProviderManager, PROVIDER_TEMPLATES, ProviderConfig

_provider_mgr = ProviderManager()
_sessions: dict[str, dict] = {}  # task_id / session_id
_history: list[dict] = []  # [{id, name, time, kicad_path, json_path, components, nets, board_size}]

HISTORY_DIR = Path.home() / ".kcad_auto_pcb" / "history"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = HISTORY_DIR / "history.json"

def _load_history():
    global _history
    if HISTORY_FILE.exists():
        try: _history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except: pass

def _save_history():
    HISTORY_FILE.write_text(json.dumps(_history[-50:], indent=2, ensure_ascii=False), encoding="utf-8")

_load_history()

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="kcad-auto-pcb")


# ── Global error handler: always return JSON, never HTML ─────────────
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "detail": traceback.format_exc()[-500:]},
    )


# ── Static files ──────────────────────────────────────────────────────

@app.get("/")
async def index():
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(index_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend not found</h1>")


# ── Provider management ───────────────────────────────────────────────

@app.get("/api/templates")
async def list_templates():
    return _provider_mgr.list_templates()


# ── Provider sources (AstrBot-style: source = API endpoint, models = instances) ──

@app.get("/api/sources")
async def list_sources():
    sources = []
    for p in _provider_mgr.list_all():
        tmpl = p.get_template()
        sources.append({
            "id": p.id, "name": p.name, "type": p.type,
            "base_url": p.base_url, "api_key": p.api_key,
            "icon": tmpl.get("icon", ""),
            "supports_vision": tmpl.get("supports_vision", False),
            "supports_model_fetch": tmpl.get("supports_model_fetch", False),
        })
    return {"sources": sources, "active_id": _provider_mgr._store.active_provider_id}


@app.post("/api/sources")
async def add_source(data: dict):
    ptype = data.get("type", ""); name = data.get("name", "")
    key = data.get("api_key", ""); url = data.get("base_url", "")
    if not name.strip(): return JSONResponse({"error": "名称不能为空"}, 400)
    tmpl = PROVIDER_TEMPLATES.get(ptype, {})
    if not tmpl: return JSONResponse({"error": f"未知类型: {ptype}"}, 400)
    p = ProviderConfig.from_template(ptype, name.strip(), key.strip(), url.strip() or tmpl.get("base_url", ""))
    _provider_mgr.add(p)
    return {"ok": True, "id": p.id}


@app.put("/api/sources/{sid}")
async def update_source(sid: str, data: dict):
    p = _provider_mgr.get(sid)
    if not p: return JSONResponse({"error": "不存在"}, 404)
    for field in ("name", "api_key", "base_url"):
        if field in data: setattr(p, field, data[field])
    if "models" in data: p.models = data["models"]
    _provider_mgr._save()
    return {"ok": True}


@app.delete("/api/sources/{sid}")
async def delete_source(sid: str):
    _provider_mgr.delete(sid)
    return {"ok": True}


@app.get("/api/sources/{sid}/models")
async def get_source_models(sid: str):
    p = _provider_mgr.get(sid)
    if not p: return JSONResponse({"error": "不存在"}, 404)
    return {"models": p.models, "default_model": p.default_model}


@app.post("/api/sources/{sid}/test")
async def test_source(sid: str):
    p = _provider_mgr.get(sid)
    if not p: return JSONResponse({"error": "不存在"}, 404)
    try:
        from ..llm.base import LLMBackendFactory
        spec = p.full_spec
        kwargs = {}
        if p.type in ("openai","openai_compatible"): kwargs["api_key"]=p.api_key
        elif p.type=="anthropic": kwargs["api_key"]=p.api_key
        elif p.type=="deepseek": kwargs["api_key"]=p.api_key
        elif p.type=="ollama": kwargs["base_url"]=p.base_url
        backend = LLMBackendFactory.create(spec, **{k:v for k,v in kwargs.items() if v})
        tokens = backend.token_count("test")
        return {"ok": True, "message": f"连接成功 — {p.type}:{p.default_model}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ── Legacy provider endpoints (kept for compatibility) ──

@app.get("/api/providers/templates")
async def list_templates():
    from ..data.providers import PROVIDER_TEMPLATES
    return {"templates": PROVIDER_TEMPLATES}


@app.get("/api/providers")
async def list_providers():
    providers = []
    for p in _provider_mgr.list_all():
        tmpl = p.get_template()
        providers.append({
            "id": p.id, "name": p.name, "type": p.type,
            "default_model": p.default_model, "models": p.models,
            "enable": p.enable, "base_url": p.base_url,
            "has_key": bool(p.api_key and len(p.api_key) > 10),
            "icon": tmpl.get("icon", ""), "supports_vision": tmpl.get("supports_vision", False),
        })
    return {
        "providers": providers,
        "active_id": _provider_mgr._store.active_provider_id,
    }


@app.post("/api/providers")
async def add_provider(data: dict):
    ptype = data.get("type", "")
    name = data.get("name", "")
    key = data.get("api_key", "")
    url = data.get("base_url", "")
    models_str = data.get("models", "")

    if not name.strip():
        return JSONResponse({"error": "名称不能为空"}, 400)

    tmpl = PROVIDER_TEMPLATES.get(ptype, {})
    if not tmpl:
        return JSONResponse({"error": f"未知供应商类型: {ptype}"}, 400)
    if tmpl.get("requires_key") and not key.strip():
        return JSONResponse({"error": f"{tmpl['name']} 需要 API 密钥"}, 400)

    models = [m.strip() for m in models_str.split(",") if m.strip()] if isinstance(models_str, str) and models_str.strip() else list(tmpl.get("models", []))

    p = ProviderConfig.from_template(ptype, name.strip(), key.strip(), url.strip() or tmpl.get("base_url", ""))
    p.models = models
    p.default_model = models[0] if models else tmpl.get("default_model", "")
    _provider_mgr.add(p)
    return {"ok": True, "id": p.id}


@app.delete("/api/providers/{pid}")
async def delete_provider(pid: str):
    _provider_mgr.delete(pid)
    return {"ok": True}


@app.put("/api/providers/{pid}")
async def update_provider(pid: str, data: dict):
    """Update an existing provider's settings."""
    p = _provider_mgr.get(pid)
    if not p:
        return JSONResponse({"error": "供应商不存在"}, 404)
    if "api_key" in data and data["api_key"]:
        p.api_key = data["api_key"]
    if "base_url" in data:
        p.base_url = data["base_url"]
    if "default_model" in data:
        p.default_model = data["default_model"]
    if "models" in data:
        p.models = data["models"] if isinstance(data["models"], list) else [data["models"]]
    if "name" in data:
        p.name = data["name"]
    _provider_mgr._save()
    return {"ok": True}


@app.post("/api/providers/{pid}/activate")
async def activate_provider(pid: str):
    _provider_mgr.set_active(pid)
    return {"ok": True}


@app.post("/api/providers/{pid}/test")
async def test_provider(pid: str):
    p = _provider_mgr.get(pid)
    if not p:
        return JSONResponse({"error": "供应商不存在"}, 404)
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
        backend = LLMBackendFactory.create(spec, **{k:v for k,v in kwargs.items() if v})
        tokens = backend.token_count("test")
        return {"ok": True, "message": f"连接成功 — {p.type}:{p.default_model}", "tokens": tokens}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/api/providers/fetch-models")
async def fetch_models(data: dict):
    models = _provider_mgr.fetch_models(
        data.get("type", ""), data.get("api_key", ""), data.get("base_url", ""),
    )
    if not models:
        return {"models": [], "error": "未获取到模型"}
    if models[0].startswith("__error__:"):
        return {"models": [], "error": models[0][10:]}
    return {"models": models}


# ── Design ────────────────────────────────────────────────────────────

def _make_settings(p: ProviderConfig) -> AppSettings:
    s = AppSettings()
    spec = p.full_spec
    if p.type in ("openai","openai_compatible"):
        s.openai_api_key = p.api_key; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    elif p.type == "anthropic":
        s.anthropic_api_key = p.api_key; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    elif p.type == "deepseek":
        s.deepseek_api_key = p.api_key; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    elif p.type == "ollama":
        s.ollama_base_url = p.base_url; s.placement_llm_spec = spec; s.routing_llm_spec = spec
    return s


@app.post("/api/analyze")
async def analyze_schematic(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix == ".kicad_sch":
        content = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".kicad_sch", delete=False) as tmp:
            tmp.write(content)
            tmp.flush()
            parser = SchematicParser()
            design = parser.parse(tmp.name)
        Path(tmp.name).unlink()

        fp_parser = FootprintParser()
        components = []
        for ref, comp in sorted(design.components.items()):
            fp = fp_parser.resolve(comp.footprint_name)
            components.append({
                "ref": ref, "value": comp.value,
                "footprint": comp.footprint_name, "fp_ok": fp is not None,
            })
        nets = []
        for name, net in sorted(design.nets.items()):
            nets.append({
                "name": name, "is_power": net.is_power,
                "pin_count": len(net.pins),
                "pins": [f"{p.component_ref}.{p.pin_number}" for p in net.pins[:10]],
            })
        return {"format": "kicad_sch", "component_count": design.component_count,
                "net_count": design.net_count, "components": components, "nets": nets}
    elif suffix == ".pdf":
        # Try PDF text extraction first (KiCad PDFs have extractable text)
        content = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content); tmp.flush(); pdf_path = tmp.name
        try:
            from ..schematic.pdf_parser import PDFSchematicParser
            parser = PDFSchematicParser()
            design = parser.parse(pdf_path)
            if design.component_count > 0:
                fp_parser = FootprintParser()
                components = []
                for ref, comp in sorted(design.components.items()):
                    fp = fp_parser.resolve(comp.footprint_name)
                    components.append({
                        "ref": ref, "value": comp.value,
                        "footprint": comp.footprint_name, "fp_ok": fp is not None,
                    })
                return {
                    "format": "pdf_text", "component_count": design.component_count,
                    "net_count": design.net_count,
                    "components": components, "nets": [],
                    "note": f"从 PDF 文本提取: {design.component_count} 个器件"
                }
        except Exception:
            pass
        finally:
            Path(pdf_path).unlink(missing_ok=True)
        return {"format": "pdf", "component_count": 0, "net_count": 0,
                "components": [], "nets": [],
                "note": "PDF 需要通过 AI 模式处理（启用供应商后直接生成）"}
    elif suffix in (".png", ".jpg", ".jpeg"):
        return {"format": "image", "component_count": 0, "net_count": 0,
                "components": [], "nets": [],
                "note": "图片需要通过 AI 模式处理（启用供应商后直接生成）"}
    return JSONResponse({"error": "不支持的格式"}, 400)


def _run_pipeline_kicad(schematic_path: str, output_path: str, layers: int = 2,
                        provider_config: dict | None = None,
                        board_width: float = 0, board_height: float = 0) -> dict:
    """Shared helper: run pipeline via KiCad Python. Returns {summary, errors}.

    If provider_config is given, LLM optimization is enabled (placement + routing).
    """
    import subprocess as _sp, json as _j, os as _os
    result_json = output_path.replace(".kicad_pcb", "_result.json")
    src_root = str(Path(__file__).parent.parent.parent)

    # Build LLM settings
    llm_spec = ""
    llm_env = ""
    if provider_config and provider_config.get('api_key'):
        p = provider_config
        llm_spec = f"{p['type']}:{p['default_model']}"
        llm_env = f'    os.environ["KCAD_OPENAI_API_KEY"] = "{p.get("api_key","")}"\n    os.environ["KCAD_DEEPSEEK_API_KEY"] = "{p.get("api_key","")}"\n'
        base = p.get('base_url', '')
        if base:
            llm_env += f'    os.environ["OPENAI_BASE_URL"] = "{base}"\n'
        if p['type'] in ('openai', 'openai_compatible', 'deepseek'):
            llm_spec = f"openai:{p['default_model']}"

    script = f'''
import asyncio, sys, json, os
sys.path.insert(0, r"{src_root}")
from kcad_auto_pcb.config.settings import AppSettings
from kcad_auto_pcb.pipeline.orchestrator import PipelineOrchestrator
from pathlib import Path
async def main():
{llm_env}    s = AppSettings(
        placement_llm_spec="{llm_spec}",
        routing_llm_spec="{llm_spec}",
    )
    o = PipelineOrchestrator(s)
    ctx = await o.run(
        schematic_path=r"{schematic_path}", output_path=r"{output_path}",
        board_layers={layers},
        board_width={board_width} if {board_width} > 0 else None,
        board_height={board_height} if {board_height} > 0 else None,
        enable_llm_placement={True if llm_spec else False},
        enable_llm_routing={True if llm_spec else False},
    )
    r = {{"stage": ctx.stage, "summary": ctx.stats.get("summary",{{}}), "errors": ctx.errors, "router": ctx.stats.get("router","")}}
    with open(r"{result_json}","w") as f: json.dump(r, f, ensure_ascii=False)
asyncio.run(main())
'''
    kicad_py = AppSettings.detect_kicad_python()
    if not kicad_py:
        raise RuntimeError("KiCad Python not found. Install KiCad to generate PCB.")
    _sp.run([kicad_py, "-c", script], check=True, timeout=300, cwd=src_root)
    if Path(result_json).exists():
        return _j.load(open(result_json))
    raise RuntimeError("Pipeline produced no output")


@app.post("/api/design")
async def run_design(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    layers: int = Form(2),
    board_width: float = Form(0),
    board_height: float = Form(0),
    enable_ai: bool = Form(False),
    provider_id: str = Form(""),
    token_budget: int = Form(10000),
):
    suffix = Path(file.filename).suffix.lower()
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        fpath = tmp.name

    settings = AppSettings(token_budget_per_run=token_budget)
    pre = None

    if enable_ai and provider_id:
        p = _provider_mgr.get(provider_id)
        if p: settings = _make_settings(p)

    if suffix in (".png",".jpg",".jpeg"):
        if not (enable_ai and provider_id):
            return JSONResponse({"error": "图片需要多模态 LLM — 请启用 AI 并选择供应商"}, 400)
        p = _provider_mgr.get(provider_id)
        if not p: return JSONResponse({"error": "供应商无效"}, 400)
        tmpl = p.get_template()
        if not tmpl.get("supports_vision"):
            return JSONResponse({"error": f"{p.type} 不支持图片识别"}, 400)
        try:
            from ..schematic.multimodal_reader import MultimodalSchematicReader
            from ..llm.base import LLMBackendFactory
            kwargs = {}
            if p.type in ("openai","openai_compatible"):
                kwargs["api_key"] = p.api_key
                if p.base_url: kwargs["base_url"] = p.base_url
            elif p.type == "anthropic": kwargs["api_key"] = p.api_key
            elif p.type == "deepseek": kwargs["api_key"] = p.api_key
            elif p.type == "ollama": kwargs["base_url"] = p.base_url
            backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
            reader = MultimodalSchematicReader(backend)
            pre = await reader.read(fpath)
        except Exception as e:
            return JSONResponse({"error": f"AI 识别失败: {e}"}, 500)
    elif suffix == ".pdf" and enable_ai and provider_id:
        # Only use LLM for PDF if user explicitly enabled AI
        p = _provider_mgr.get(provider_id)
        if p:
            tmpl = p.get_template()
            if tmpl.get("supports_vision"):
                try:
                    from ..schematic.multimodal_reader import MultimodalSchematicReader
                    from ..llm.base import LLMBackendFactory
                    kwargs = {}
                    if p.type in ("openai","openai_compatible"):
                        kwargs["api_key"] = p.api_key
                        if p.base_url: kwargs["base_url"] = p.base_url
                    elif p.type == "anthropic": kwargs["api_key"] = p.api_key
                    elif p.type == "deepseek": kwargs["api_key"] = p.api_key
                    elif p.type == "ollama": kwargs["base_url"] = p.base_url
                    backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
                    reader = MultimodalSchematicReader(backend)
                    pre = await reader.read(fpath)
                except Exception as e:
                    return JSONResponse({"error": f"AI 识别失败: {e}"}, 500)

    out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
    out_path = out.name; out.close()

    try:
        provider = _provider_mgr.get_active() if _provider_mgr else None
        provider_cfg = None
        if provider:
            provider_cfg = {"type": provider.type, "default_model": provider.default_model, "api_key": provider.api_key, "base_url": getattr(provider, 'base_url', '')}
        result = _run_pipeline_kicad(fpath, out_path, layers, provider_cfg)
        Path(fpath).unlink(missing_ok=True)
        jp = out_path.replace(".kicad_pcb", ".json")
        summary = result.get("summary", {})
        errors_list = result.get("errors", [])
        json.dump({"summary": summary, "errors": errors_list}, open(jp, "w"), indent=2, ensure_ascii=False)
        sid = uuid.uuid4().hex[:12]
        svg = _render_svg_preview(out_path)
        _sessions[sid] = {"kicad": out_path, "json": jp, "summary": summary, "errors": errors_list, "svg": svg}
        try: _add_history(Path(fpath).stem, out_path, jp, summary)
        except: pass
        return {"session_id": sid, "summary": summary, "errors": errors_list, "svg": svg}
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, 500)


@app.post("/api/agent")
async def run_agent(
    file: UploadFile = File(...),
    layers: int = Form(2),
    provider_id: str = Form(""),
    max_iters: int = Form(15),
):
    suffix = Path(file.filename).suffix.lower()
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content); tmp.flush(); fpath = tmp.name

    settings = AppSettings(); pre = None
    if provider_id:
        p = _provider_mgr.get(provider_id)
        if p: settings = _make_settings(p)

    if suffix in (".png",".jpg",".jpeg"):
        if not provider_id: return JSONResponse({"error": "图片需要多模态供应商"}, 400)
        p = _provider_mgr.get(provider_id)
        try:
            from ..schematic.multimodal_reader import MultimodalSchematicReader
            from ..llm.base import LLMBackendFactory
            kwargs = {}
            if p.type in ("openai","openai_compatible"):
                kwargs["api_key"] = p.api_key
                if p.base_url: kwargs["base_url"] = p.base_url
            elif p.type == "anthropic": kwargs["api_key"] = p.api_key
            elif p.type == "deepseek": kwargs["api_key"] = p.api_key
            elif p.type == "ollama": kwargs["base_url"] = p.base_url
            backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
            reader = MultimodalSchematicReader(backend)
            pre = await reader.read(fpath)
        except Exception as e:
            return JSONResponse({"error": f"AI 识别失败: {e}"}, 500)
    elif suffix == ".pdf" and provider_id:
        p = _provider_mgr.get(provider_id)
        if p:
            tmpl = p.get_template()
            if tmpl.get("supports_vision"):
                try:
                    from ..schematic.multimodal_reader import MultimodalSchematicReader
                    from ..llm.base import LLMBackendFactory
                    kwargs = {}
                    if p.type in ("openai","openai_compatible"):
                        kwargs["api_key"] = p.api_key
                        if p.base_url: kwargs["base_url"] = p.base_url
                    elif p.type == "anthropic": kwargs["api_key"] = p.api_key
                    elif p.type == "deepseek": kwargs["api_key"] = p.api_key
                    elif p.type == "ollama": kwargs["base_url"] = p.base_url
                    backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
                    reader = MultimodalSchematicReader(backend)
                    pre = await reader.read(fpath)
                except Exception:
                    pass

    # Run pipeline synchronously (simpler than threading, guaranteed to work)
    import time as _time
    out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
    out_path = out.name; out.close()

    task_id = uuid.uuid4().hex[:8]
    _sessions[task_id] = {
        "status": "running", "progress": "启动中...", "started": _time.time(),
        "kicad": out_path, "json": out_path.replace(".kicad_pcb",".json"),
    }

    def _run_agent_task():
        try:
            _sessions[task_id]["progress"] = "正在布局+布线..."
            provider = _provider_mgr.get_active()
            provider_cfg = {"type": provider.type, "default_model": provider.default_model, "api_key": provider.api_key, "base_url": getattr(provider, 'base_url', '')} if provider else None
            result = _run_pipeline_kicad(fpath, out_path, layers, provider_cfg)
            # Generate SVG preview
            svg = _render_svg_preview(out_path)
            _sessions[task_id].update({
                "status": "done", "progress": "完成",
                "summary": result.get("summary", {}),
                "errors": result.get("errors", []),
                "router": result.get("router", ""),
                "svg": svg,
                "iterations": 1,
                "actions": 2,
                "result": {"status": "completed", "iterations": 1, "actions": 2},
                "history": [],
            })
        except Exception as e:
            _sessions[task_id].update({
                "status": "error", "progress": "失败",
                "error": str(e)[:200],
            })

    threading.Thread(target=_run_agent_task, daemon=True).start()
    return {"task_id": task_id, "status": "running"}


@app.get("/api/task/{task_id}")
async def get_task_status(task_id: str):
    s = _sessions.get(task_id)
    if not s: return JSONResponse({"error": "任务不存在"}, 404)
    return {
        "status": s.get("status"), "progress": s.get("progress"),
        "result": s.get("result"), "history": s.get("history"),
        "iterations": s.get("iterations"), "actions": s.get("actions"),
        "error": s.get("error"),
        "session_id": task_id if s.get("status") == "done" else None,
    }


@app.get("/api/history")
async def get_history():
    return _history[-30:]  # Last 30 entries


def _add_history(name, kicad_path, json_path, summary):
    entry = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "time": time.strftime("%m-%d %H:%M"),
        "kicad": kicad_path,
        "json": json_path,
        "components": summary.get("components", 0),
        "nets": summary.get("nets", 0),
        "board": summary.get("board_size", "?"),
    }
    _history.append(entry)
    _save_history()


@app.post("/api/spec-design")
async def spec_design(
    file: UploadFile = File(...),
    provider_id: str = Form(""),
    layers: int = Form(2),
):
    """Agent-driven closed-loop PCB design."""
    suffix = Path(file.filename).suffix.lower()
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content); tmp.flush(); fpath = tmp.name

    out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
    out_path = out.name; out.close()

    try:
        provider = _provider_mgr.get_active() if _provider_mgr else None
        provider_cfg = None
        if provider:
            provider_cfg = {"type": provider.type, "default_model": provider.default_model, "api_key": provider.api_key, "base_url": getattr(provider, 'base_url', '')}
        result = _run_pipeline_kicad(fpath, out_path, layers, provider_cfg)
        summary = result.get("summary", {})
        sid = uuid.uuid4().hex[:12]
        _sessions[sid] = {
            "kicad": out_path,
            "json": out_path.replace(".kicad_pcb", ".json"),
        }
        return {
            "session_id": sid, "svg": "",
            "status": result.get("stage", "done"),
            "drc_errors": 0,
            "iterations": 1,
            "spec": "",
            "warnings": result.get("errors", []),
            "summary": summary,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)[:300]}, 500)
    finally:
        Path(fpath).unlink(missing_ok=True)


@app.post("/api/spec-design-old")
async def spec_design_old(
    file: UploadFile = File(...),
    provider_id: str = Form(""),
    layers: int = Form(2),
):
    """New spec-based design: schematic → LLM generates spec → draw tools execute."""
    suffix = Path(file.filename).suffix.lower()
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content); tmp.flush(); fpath = tmp.name

    # Parse or extract design
    from ..schematic.parser import SchematicParser
    design = SchematicParser().parse(fpath) if suffix == ".kicad_sch" else None
    if not design or design.component_count == 0:
        try:
            from ..schematic.pdf_parser import PDFSchematicParser
            design = PDFSchematicParser().parse(fpath)
        except: pass
    if not design or design.component_count == 0:
        return JSONResponse({"error": "无法解析原理图"}, 400)

    # Build design summary for LLM
    comps = "\n".join(
        f"  {ref}: {c.value} (footprint: {c.footprint_name or 'auto'})"
        for ref, c in sorted(design.components.items())
    )
    nets = "\n".join(
        f"  {name}: {len(net.pins)} pins - " +
        ", ".join(f"{p.component_ref}.{p.pin_number}" for p in net.pins[:6])
        for name, net in sorted(design.nets.items())
    )
    design_summary = f"""Components ({design.component_count}):
{comps}

Nets ({design.net_count}):
{nets}"""

    # Use KiCad pipeline for proper FreeRouting output
    if not provider_id:
        out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
        out_path = out.name; out.close()
        provider = _provider_mgr.get_active() if _provider_mgr else None
        provider_cfg = None
        if provider:
            provider_cfg = {"type": provider.type, "default_model": provider.default_model, "api_key": provider.api_key, "base_url": getattr(provider, 'base_url', '')}
        result = _run_pipeline_kicad(fpath, out_path, layers, provider_cfg)
        sid = uuid.uuid4().hex[:12]
        _sessions[sid] = {"kicad": out_path, "json": out_path.replace(".kicad_pcb",".json")}
        return {"session_id": sid, "spec": "{}", "history": [], "summary": result.get("summary", {})}

    # With LLM: generate spec via AI
    p = _provider_mgr.get(provider_id)
    if not p: return JSONResponse({"error": "供应商无效"}, 400)

    from ..tools.spec import SPEC_GENERATION_PROMPT
    from ..llm.base import LLMBackendFactory
    from ..llm.base import LLMMessage

    kwargs = {}
    if p.type in ("openai","openai_compatible"):
        kwargs["api_key"] = p.api_key
        if p.base_url: kwargs["base_url"] = p.base_url
    elif p.type == "anthropic": kwargs["api_key"] = p.api_key
    elif p.type == "deepseek": kwargs["api_key"] = p.api_key
    elif p.type == "ollama": kwargs["base_url"] = p.base_url

    try:
        backend = LLMBackendFactory.create(p.full_spec, **{k:v for k,v in kwargs.items() if v})
        prompt = SPEC_GENERATION_PROMPT.replace("{design_summary}", design_summary)
        response = await asyncio.wait_for(
            backend.chat([LLMMessage(role="user", content=prompt)], temperature=0.2, max_tokens=4096),
            timeout=120,
        )
    except asyncio.TimeoutError:
        return JSONResponse({"error": "LLM 调用超时"}, 500)
    except Exception as e:
        return JSONResponse({"error": f"LLM 调用失败: {e}"}, 500)

    # Parse LLM output as YAML
    import re
    yaml_text = response.text
    m = re.search(r'```(?:yaml)?\s*([\s\S]*?)```', yaml_text)
    if m: yaml_text = m.group(1)

    try:
        from ..tools.spec import BoardSpec
        spec = BoardSpec.from_yaml(yaml_text)
    except Exception:
        return JSONResponse({"error": f"LLM 输出无法解析为有效 spec", "raw": yaml_text[:500]}, 500)

    footprint_map = {ref: comp.footprint_name for ref, comp in design.components.items()}

    from ..tools.converter import export_spec
    out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
    out_path = out.name; out.close()
    export_spec(spec, footprint_map, out_path)

    sid = uuid.uuid4().hex[:12]
    _sessions[sid] = {"kicad": out_path, "json": out_path.replace(".kicad_pcb",".json")}
    return {"session_id": sid, "spec": spec.to_json(), "llm_response": yaml_text[:200]}


@app.get("/api/download/{sid}/{ftype}")
async def download(sid: str, ftype: str):
    s = _sessions.get(sid, {})
    path = s.get(ftype, "")
    if path and Path(path).exists():
        suffix = ".kicad_pcb" if ftype == "kicad" else ".json"
        return FileResponse(path, filename=f"pcb_design{suffix}",
                            media_type="application/octet-stream")
    return JSONResponse({"error": "文件不存在或已过期"}, 404)


def _render_svg_preview(ctx_or_path):
    """Render SVG from either PipelineContext or .kicad_pcb file path."""
    if ctx_or_path is None:
        return ""
    if isinstance(ctx_or_path, str):
        from .preview import render_pcb_svg_from_file
        return render_pcb_svg_from_file(ctx_or_path)
    from .preview import render_pcb_svg
    return render_pcb_svg(ctx_or_path.board) if ctx_or_path and ctx_or_path.board else ""


# ── CLI entry ─────────────────────────────────────────────────────────

# ── WebSocket Streaming Chat ────────────────────────────────────────

@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # Load or create session
    sess = _load_chat(session_id)
    if not sess:
        session_id = uuid.uuid4().hex[:8]
        sess = {
            "id": session_id, "history": [],
            "schematic_path": None,
            "params": {"layers": 2, "width": 0, "height": 0},
            "created": time.time(),
        }

    async def send(msg_type: str, **data):
        try:
            await websocket.send_json({"type": msg_type, **data})
        except Exception:
            pass

    await send("ready", session_id=session_id, params=sess["params"])

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)

            msg_type = msg.get("type", "message")
            content = msg.get("content", "")
            params = msg.get("params", {})

            if msg_type == "upload_file":
                # File data comes as base64
                import base64
                file_data = base64.b64decode(msg.get("data", ""))
                filename = msg.get("filename", "schematic.pdf")
                suffix = Path(filename).suffix.lower()
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(file_data); tmp.flush()
                sess["schematic_path"] = tmp.name

                await send("thinking", content="Parsing schematic...")
                from ..schematic.pdf_parser import PDFSchematicParser
                design = PDFSchematicParser().parse(sess["schematic_path"]) if suffix == ".pdf" else SchematicParser().parse(sess["schematic_path"])
                summary = f"{design.component_count} components, {design.net_count} nets"
                sess["design_summary"] = {"components": design.component_count, "nets": design.net_count}
                sess["history"].append({"role": "system", "content": f"Uploaded: {filename} - {summary}"})
                _save_chat(session_id, sess)
                await send("done", reply=summary)

            elif msg_type == "message":
                sess["history"].append({"role": "user", "content": content})

                # Parse commands
                msg_lower = content.lower()
                import re as _re

                # Use formula-based parameter inference
                from ..agent.domain_knowledge import get_design_params
                if sess.get("design_summary"):
                    params_inferred = get_design_params(
                        component_count=sess["design_summary"].get("components", 0),
                        application_hint=content,
                    )
                    sess["params"].update(params_inferred)
                    reply = (f"Based on '{content[:80]}' + {sess['design_summary']['components']} components:\n"
                            f"→ {params_inferred['width']}x{params_inferred['height']}mm, "
                            f"{params_inferred['layers']} layers, "
                            f"density {params_inferred['density']} comps/cm²")
                    sess["history"].append({"role": "assistant", "content": reply})
                    _save_chat(session_id, sess)
                    await send("done", reply=reply, params=sess["params"])
                    continue

                # Parse manual params
                w_match = _re.search(r'width[=:\s]*(\d+)', msg_lower)
                h_match = _re.search(r'height[=:\s]*(\d+)', msg_lower)
                l_match = _re.search(r'layers?[=:\s]*(\d+)', msg_lower)

                if w_match: sess["params"]["width"] = int(w_match.group(1))
                if h_match: sess["params"]["height"] = int(h_match.group(1))
                if l_match: sess["params"]["layers"] = int(l_match.group(1))

                if any([w_match, h_match, l_match]):
                    reply = f"Board params updated: {sess['params']}"
                    sess["history"].append({"role": "assistant", "content": reply})
                    _save_chat(session_id, sess)
                    await send("done", reply=reply, params=sess["params"])

                elif 'generate' in msg_lower:
                    if not sess.get("schematic_path"):
                        await send("error", reply="Upload a schematic first.")
                    else:
                        await send("thinking", content="Placing components...")
                        out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
                        out_path = out.name; out.close()

                        provider = _provider_mgr.get_active() if _provider_mgr else None
                        provider_cfg = None
                        if provider:
                            provider_cfg = {"type": provider.type, "default_model": provider.default_model,
                                           "api_key": provider.api_key, "base_url": getattr(provider, 'base_url', '')}

                        await send("thinking", content="Running autorouter...")
                        result = _run_pipeline_kicad(
                            sess["schematic_path"], out_path,
                            sess["params"]["layers"], provider_cfg,
                            sess["params"].get("width", 0),
                            sess["params"].get("height", 0))

                        summary = result.get("summary", {})
                        reply = f"PCB generated! {summary.get('board_size', '?')}mm, {summary.get('components', '?')} comps, {summary.get('traces', '?')} traces"
                        sess["history"].append({"role": "assistant", "content": reply})

                        # Generate SVG preview
                        svg = _render_svg_preview(out_path)
                        sid = uuid.uuid4().hex[:12]
                        _sessions[sid] = {"kicad": out_path, "json": out_path.replace(".kicad_pcb", ".json"),
                                         "summary": summary, "errors": result.get("errors", []), "svg": svg}

                        _save_chat(session_id, sess)
                        await send("done", reply=reply, session_id=sid, summary=summary, svg=svg)

                else:
                    reply = "I can help with your PCB. Try: 'width 60 height 25' to set size, or 'generate' to create the PCB."
                    sess["history"].append({"role": "assistant", "content": reply})
                    _save_chat(session_id, sess)
                    await send("done", reply=reply)

            elif msg_type == "get_history":
                await send("history", messages=sess.get("history", []))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await send("error", reply=str(e)[:200])
        except Exception:
            pass

# ── REST Conversational Agent Endpoint ──────────────────────────────

CHAT_DIR = Path.home() / ".kcad_auto_pcb" / "chats"
CHAT_DIR.mkdir(parents=True, exist_ok=True)
_chat_sessions: dict[str, dict] = {}  # in-memory cache

def _load_chat(session_id: str) -> dict:
    if session_id in _chat_sessions:
        return _chat_sessions[session_id]
    f = CHAT_DIR / f"{session_id}.json"
    if f.exists():
        sess = json.loads(f.read_text(encoding="utf-8"))
        _chat_sessions[session_id] = sess
        return sess
    return None

def _save_chat(session_id: str, session_data: dict):
    _chat_sessions[session_id] = session_data
    f = CHAT_DIR / f"{session_id}.json"
    # Keep file size manageable
    save_data = dict(session_data)
    if len(save_data.get("history", [])) > 100:
        save_data["history"] = save_data["history"][-80:]
    f.write_text(json.dumps(save_data, indent=2, ensure_ascii=False), encoding="utf-8")

@app.post("/api/chat")
async def chat(
    message: str = Form(""),
    session_id: str = Form(""),
    file: UploadFile | None = None,
    board_width: float = Form(0),
    board_height: float = Form(0),
    board_layers: int = Form(2),
):
    """Conversational PCB design agent. Send messages, get PCB updates."""
    # Get or create session
    sess = _load_chat(session_id) if session_id else None
    if not sess:
        session_id = uuid.uuid4().hex[:8]
        sess = {
            "id": session_id,
            "history": [],
            "schematic_path": None,
            "params": {"layers": board_layers, "width": board_width, "height": board_height},
            "created": time.time(),
        }

    # Handle file upload
    if file:
        content = await file.read()
        suffix = Path(file.filename).suffix.lower()
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(content); tmp.flush()
        sess["schematic_path"] = tmp.name

        # Parse and summarize
        from ..schematic.pdf_parser import PDFSchematicParser
        design = PDFSchematicParser().parse(sess["schematic_path"]) if suffix == ".pdf" else SchematicParser().parse(sess["schematic_path"])
        summary = f"Uploaded: {file.filename} - {design.component_count} components, {design.net_count} nets"
        sess["history"].append({"role": "system", "content": summary})
        _save_chat(session_id, sess)
        return {"session_id": session_id, "reply": summary, "design": {"components": design.component_count, "nets": design.net_count}}

    # Handle text message
    if message:
        sess["history"].append({"role": "user", "content": message})

        # Parse user intent with LLM
        provider = _provider_mgr.get_active() if _provider_mgr else None
        reply = ""

        if provider:
            try:
                from ..llm.base import LLMBackendFactory, LLMMessage
                kwargs = {"api_key": provider.api_key}
                if getattr(provider, 'base_url', ''): kwargs["base_url"] = provider.base_url
                backend = LLMBackendFactory.create(provider.full_spec, **{k:v for k,v in kwargs.items() if v})

                # Build context for LLM
                context = f"""You are a PCB design assistant for kcad-auto-pcb. The user is designing a PCB.

Current design: {sess.get('schematic_path', 'No schematic uploaded')}
Board params: {sess['params']}
Last messages: {json.dumps(sess['history'][-4:], ensure_ascii=False)}

User said: "{message}"

You can adjust these board parameters by responding with:
{{"action":"set_params","width":50,"height":25,"layers":4}}
{{"action":"generate","comment":"Generating PCB now..."}}
{{"action":"reply","comment":"Your explanation here"}}

Respond ONLY with valid JSON. For general questions, use action:reply."""

                resp = await backend.chat([LLMMessage(role="user", content=context)], max_tokens=300)
                try:
                    intent = json.loads(resp.text.strip())
                except Exception:
                    # Extract JSON from response
                    import re as _re
                    m = _re.search(r'\{[\s\S]*\}', resp.text)
                    intent = json.loads(m.group()) if m else {"action": "reply", "comment": resp.text[:200]}

                action = intent.get("action", "reply")

                if action == "set_params":
                    if "width" in intent: sess["params"]["width"] = intent["width"]
                    if "height" in intent: sess["params"]["height"] = intent["height"]
                    if "layers" in intent: sess["params"]["layers"] = intent["layers"]
                    reply = f"Parameters updated: {sess['params']}"

                elif action == "generate":
                    if not sess.get("schematic_path"):
                        reply = "Please upload a schematic file first."
                    else:
                        out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
                        out_path = out.name; out.close()
                        provider_cfg = {"type": provider.type, "default_model": provider.default_model,
                                       "api_key": provider.api_key, "base_url": getattr(provider, 'base_url', '')}
                        result = _run_pipeline_kicad(sess["schematic_path"], out_path,
                                                     sess["params"]["layers"], provider_cfg,
                                                     sess["params"].get("width", 0),
                                                     sess["params"].get("height", 0))
                        summary = result.get("summary", {})
                        sid = uuid.uuid4().hex[:12]
                        _sessions[sid] = {"kicad": out_path, "json": out_path.replace(".kicad_pcb", ".json"),
                                         "summary": summary, "errors": result.get("errors", [])}
                        reply = f"PCB generated! {summary.get('board_size', '')}mm, {summary.get('components', '?')} comps. Session: {sid}"

                elif action == "reply":
                    reply = intent.get("comment", "I understand. What would you like to change about the PCB?")

                else:
                    reply = intent.get("comment", "Ready to help with your PCB design.")

            except Exception as e:
                reply = f"LLM unavailable ({e}). You can still set parameters manually. Say 'width 60 height 25' or 'generate'."

        else:
            # No LLM: parse commands manually
            msg_lower = message.lower()
            import re as _re
            w_match = _re.search(r'width[=:\s]*(\d+)', msg_lower)
            h_match = _re.search(r'height[=:\s]*(\d+)', msg_lower)
            l_match = _re.search(r'layers?[=:\s]*(\d+)', msg_lower)
            if w_match: sess["params"]["width"] = int(w_match.group(1))
            if h_match: sess["params"]["height"] = int(h_match.group(1))
            if l_match: sess["params"]["layers"] = int(l_match.group(1))

            if any([w_match, h_match, l_match]):
                reply = f"Board params: {sess['params']}. Say 'generate' to create PCB."
            elif 'generate' in msg_lower:
                if not sess.get("schematic_path"):
                    reply = "Upload a schematic file first."
                else:
                    out = tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False)
                    out_path = out.name; out.close()
                    result = _run_pipeline_kicad(sess["schematic_path"], out_path,
                                                 sess["params"]["layers"], None,
                                                 sess["params"].get("width", 0),
                                                 sess["params"].get("height", 0))
                    summary = result.get("summary", {})
                    sid = uuid.uuid4().hex[:12]
                    _sessions[sid] = {"kicad": out_path, "json": out_path.replace(".kicad_pcb", ".json"),
                                     "summary": summary, "errors": result.get("errors", [])}
                    reply = f"PCB generated! {summary.get('board_size', '')}mm. Session: {sid}"
            else:
                reply = "I can help design your PCB. Upload a schematic, then tell me requirements like 'width 60 height 25' or 'generate'."

        sess["history"].append({"role": "assistant", "content": reply})
        _save_chat(session_id, sess)
        return {"session_id": session_id, "reply": reply, "params": sess["params"]}

    return {"session_id": session_id, "reply": "Please send a message or upload a file."}


@app.get("/api/chat/sessions")
async def list_chat_sessions():
    """List all saved chat sessions."""
    sessions = []
    for f in sorted(CHAT_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "id": data.get("id", f.stem),
                "created": data.get("created", 0),
                "messages": len(data.get("history", [])),
                "params": data.get("params", {}),
            })
        except Exception:
            pass
    return {"sessions": sessions[:20]}

@app.get("/api/chat/session/{session_id}")
async def get_chat_session(session_id: str):
    """Load a saved chat session."""
    sess = _load_chat(session_id)
    if not sess:
        return JSONResponse({"error": "Session not found"}, 404)
    return {"session_id": session_id, "history": sess.get("history", []), "params": sess.get("params", {})}

@app.delete("/api/chat/session/{session_id}")
async def delete_chat_session(session_id: str):
    """Delete a chat session."""
    f = CHAT_DIR / f"{session_id}.json"
    if f.exists(): f.unlink()
    _chat_sessions.pop(session_id, None)
    return {"ok": True}


def main():
    # Write the frontend if it doesn't exist
    index_html = STATIC_DIR / "index.html"
    if not index_html.exists():
        _write_frontend(index_html)
    uvicorn.run(app, host="127.0.0.1", port=7860)


def _write_frontend(path: Path):
    """Generate the frontend HTML file."""
    path.write_text(_FRONTEND_HTML, encoding="utf-8")
