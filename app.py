"""FastAPI web application for the cartoon-to-slides pipeline."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sse_starlette.sse import EventSourceResponse

from pipeline_runner import run_pipeline, rerender_from_html
from project_manager import (
    PIPELINE_STEP_ORDER,
    PipelineStep,
    ProjectMeta,
    create_project,
    delete_project,
    get_first_frame_path,
    get_project,
    invalidate_steps_from,
    list_projects,
    project_dir,
    update_project,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Cartoon-to-Slides")

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "web_templates"))

# In-memory SSE queues keyed by project id
_progress_queues: dict[str, list[asyncio.Queue[dict[str, str]]]] = {}
_running_pipelines: dict[str, threading.Thread] = {}


# ---- helpers -------------------------------------------------------------


def _get_project_or_404(project_id: str) -> ProjectMeta:
    meta = get_project(project_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return meta


def _broadcast(project_id: str, data: dict[str, str]) -> None:
    for q in _progress_queues.get(project_id, []):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


def _make_progress_cb(project_id: str):
    def cb(step: PipelineStep, status: str, message: str) -> None:
        _broadcast(project_id, {
            "step": step.value,
            "status": status,
            "message": message,
        })
    return cb


# ---- HTML pages ----------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def page_projects(request: Request):
    projects = list_projects()
    thumbnails: dict[str, str | None] = {}
    for p in projects:
        fp = get_first_frame_path(p.id)
        thumbnails[p.id] = f"/api/projects/{p.id}/assets/thumbnail" if fp else None
    return templates.TemplateResponse(
        request, "projects.html",
        context={"projects": projects, "thumbnails": thumbnails},
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
async def page_project_detail(request: Request, project_id: str):
    meta = _get_project_or_404(project_id)
    work = project_dir(project_id)

    rendered_dir = os.path.join(work, "rendered_slides")
    slide_count = 0
    if os.path.isdir(rendered_dir):
        slide_count = len([f for f in os.listdir(rendered_dir) if f.endswith(".png")])

    frames_dir = os.path.join(work, "frames")
    frame_count = 0
    if os.path.isdir(frames_dir):
        frame_count = len([f for f in os.listdir(frames_dir) if f.endswith(".png")])

    pptx_path = os.path.join(work, "output.pptx")
    has_pptx = os.path.isfile(pptx_path)

    has_plan = os.path.isfile(os.path.join(work, "slide_plan.json"))
    has_video = os.path.isfile(os.path.join(work, "video.mp4"))

    is_running = project_id in _running_pipelines and _running_pipelines[project_id].is_alive()

    return templates.TemplateResponse(
        request, "project_detail.html",
        context={
            "project": meta,
            "slide_count": slide_count,
            "frame_count": frame_count,
            "has_pptx": has_pptx,
            "has_plan": has_plan,
            "has_video": has_video,
            "is_running": is_running,
            "steps": PIPELINE_STEP_ORDER,
        },
    )


@app.get("/projects/{project_id}/edit/plan", response_class=HTMLResponse)
async def page_edit_plan(request: Request, project_id: str):
    meta = _get_project_or_404(project_id)
    work = project_dir(project_id)

    plan_data = ""
    plan_path = os.path.join(work, "slide_plan.json")
    if os.path.isfile(plan_path):
        with open(plan_path, encoding="utf-8") as f:
            plan_data = f.read()

    raw_data = ""
    raw_path = os.path.join(work, "openai_raw_response.json")
    if os.path.isfile(raw_path):
        with open(raw_path, encoding="utf-8") as f:
            raw_data = f.read()

    return templates.TemplateResponse(
        request, "edit_slide_plan.html",
        context={"project": meta, "plan_data": plan_data, "raw_data": raw_data},
    )


@app.get("/projects/{project_id}/edit/html", response_class=HTMLResponse)
async def page_edit_html(request: Request, project_id: str):
    meta = _get_project_or_404(project_id)
    work = project_dir(project_id)

    html_dir = os.path.join(work, "rendered_slides", "html_debug")
    slides: list[dict[str, Any]] = []
    if os.path.isdir(html_dir):
        for f in sorted(os.listdir(html_dir)):
            if f.endswith(".html"):
                idx = int(f.replace("slide_", "").replace(".html", ""))
                slides.append({"index": idx, "filename": f})

    return templates.TemplateResponse(
        request, "edit_html.html",
        context={"project": meta, "slides": slides},
    )


@app.get("/projects/{project_id}/edit/frames", response_class=HTMLResponse)
async def page_manage_frames(request: Request, project_id: str):
    meta = _get_project_or_404(project_id)
    work = project_dir(project_id)

    frames: list[dict[str, Any]] = []
    manifest_path = os.path.join(work, "frames", "frames_manifest.json")
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        for i, fr in enumerate(manifest.get("frames", [])):
            frames.append({
                "index": i,
                "timestamp": fr.get("timestamp_seconds", 0),
                "exists": os.path.isfile(fr.get("path", "")),
            })

    return templates.TemplateResponse(
        request, "manage_frames.html",
        context={"project": meta, "frames": frames},
    )


# ---- API: project CRUD --------------------------------------------------


@app.post("/api/projects")
async def api_create_project(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "Project name is required")

    config: dict[str, Any] = {}
    for key in (
        "whisper_model", "openai_model", "reasoning_effort",
        "dalle_model", "audience", "frame_strategy",
    ):
        val = form.get(key)
        if val:
            config[key] = str(val)
    for key in ("max_slides", "max_frames", "max_vision_frames"):
        val = form.get(key)
        if val:
            try:
                config[key] = int(val)
            except ValueError:
                pass
    for key in ("openai_temperature", "interval_seconds", "frame_offset"):
        val = form.get(key)
        if val:
            try:
                config[key] = float(val)
            except ValueError:
                pass
    if form.get("no_illustrations"):
        config["no_illustrations"] = True
    if form.get("no_vision"):
        config["use_vision"] = False

    meta = create_project(name, config)
    return RedirectResponse(f"/projects/{meta.id}", status_code=303)


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str):
    if not delete_project(project_id):
        raise HTTPException(404, "Project not found")
    return JSONResponse({"ok": True})


# ---- API: video upload ---------------------------------------------------


@app.post("/api/projects/{project_id}/upload")
async def api_upload_video(project_id: str, file: UploadFile):
    meta = _get_project_or_404(project_id)
    work = project_dir(project_id)

    if not file.filename:
        raise HTTPException(400, "No file provided")

    video_path = os.path.join(work, "video.mp4")
    with open(video_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    meta.video_filename = file.filename
    from project_manager import StepState, set_step_done
    set_step_done(meta, PipelineStep.UPLOAD, f"Uploaded {file.filename}")
    update_project(meta)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ---- API: pipeline control -----------------------------------------------


@app.post("/api/projects/{project_id}/run")
async def api_run_pipeline(project_id: str):
    meta = _get_project_or_404(project_id)
    if project_id in _running_pipelines and _running_pipelines[project_id].is_alive():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            run_pipeline(meta, start_from=PipelineStep.TRANSCRIBE, progress=_make_progress_cb(project_id))
        except Exception:
            pass
        finally:
            _broadcast(project_id, {"step": "done", "status": "finished", "message": "Pipeline finished"})

    t = threading.Thread(target=_run, daemon=True)
    _running_pipelines[project_id] = t
    t.start()
    return JSONResponse({"ok": True, "message": "Pipeline started"})


@app.post("/api/projects/{project_id}/run-from/{step}")
async def api_run_from_step(project_id: str, step: str):
    meta = _get_project_or_404(project_id)
    if project_id in _running_pipelines and _running_pipelines[project_id].is_alive():
        raise HTTPException(409, "Pipeline is already running")

    try:
        start_step = PipelineStep(step)
    except ValueError:
        raise HTTPException(400, f"Invalid step: {step}")

    invalidate_steps_from(meta, start_step)

    def _run():
        try:
            run_pipeline(meta, start_from=start_step, progress=_make_progress_cb(project_id))
        except Exception:
            pass
        finally:
            _broadcast(project_id, {"step": "done", "status": "finished", "message": "Pipeline finished"})

    t = threading.Thread(target=_run, daemon=True)
    _running_pipelines[project_id] = t
    t.start()
    return JSONResponse({"ok": True, "message": f"Pipeline started from {step}"})


@app.post("/api/projects/{project_id}/rerender")
async def api_rerender(project_id: str):
    meta = _get_project_or_404(project_id)
    if project_id in _running_pipelines and _running_pipelines[project_id].is_alive():
        raise HTTPException(409, "Pipeline is already running")

    def _run():
        try:
            rerender_from_html(meta, progress=_make_progress_cb(project_id))
        except Exception:
            pass
        finally:
            _broadcast(project_id, {"step": "done", "status": "finished", "message": "Re-render finished"})

    t = threading.Thread(target=_run, daemon=True)
    _running_pipelines[project_id] = t
    t.start()
    return JSONResponse({"ok": True, "message": "Re-render started"})


# ---- API: SSE progress ---------------------------------------------------


@app.get("/api/projects/{project_id}/progress")
async def api_progress(project_id: str):
    _get_project_or_404(project_id)
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=100)
    _progress_queues.setdefault(project_id, []).append(queue)

    async def event_generator():
        try:
            while True:
                data = await queue.get()
                yield {"event": "progress", "data": json.dumps(data)}
                if data.get("status") == "finished":
                    break
        finally:
            _progress_queues.get(project_id, []).remove(queue)

    return EventSourceResponse(event_generator())


# ---- API: asset access ----------------------------------------------------


@app.get("/api/projects/{project_id}/assets/thumbnail")
async def api_thumbnail(project_id: str):
    fp = get_first_frame_path(project_id)
    if not fp or not os.path.isfile(fp):
        raise HTTPException(404, "No thumbnail")
    return FileResponse(fp, media_type="image/png")


@app.get("/api/projects/{project_id}/assets/frames")
async def api_list_frames(project_id: str):
    work = project_dir(project_id)
    manifest_path = os.path.join(work, "frames", "frames_manifest.json")
    if not os.path.isfile(manifest_path):
        return JSONResponse([])
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    result = []
    for i, fr in enumerate(manifest.get("frames", [])):
        result.append({
            "index": i,
            "timestamp_seconds": fr.get("timestamp_seconds"),
            "exists": os.path.isfile(fr.get("path", "")),
        })
    return JSONResponse(result)


@app.get("/api/projects/{project_id}/assets/frames/{idx}")
async def api_get_frame(project_id: str, idx: int):
    work = project_dir(project_id)
    manifest_path = os.path.join(work, "frames", "frames_manifest.json")
    if not os.path.isfile(manifest_path):
        raise HTTPException(404)
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    frames = manifest.get("frames", [])
    if idx < 0 or idx >= len(frames):
        raise HTTPException(404)
    path = frames[idx].get("path", "")
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


@app.post("/api/projects/{project_id}/assets/frames/{idx}")
async def api_replace_frame(project_id: str, idx: int, file: UploadFile):
    work = project_dir(project_id)
    manifest_path = os.path.join(work, "frames", "frames_manifest.json")
    if not os.path.isfile(manifest_path):
        raise HTTPException(404)
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    frames = manifest.get("frames", [])
    if idx < 0 or idx >= len(frames):
        raise HTTPException(404, "Frame index out of range")
    path = frames[idx].get("path", "")
    if not path:
        raise HTTPException(400, "No path for frame")
    with open(path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)
    return JSONResponse({"ok": True, "message": f"Frame {idx} replaced"})


@app.get("/api/projects/{project_id}/assets/slides")
async def api_list_slides(project_id: str):
    work = project_dir(project_id)
    rendered_dir = os.path.join(work, "rendered_slides")
    if not os.path.isdir(rendered_dir):
        return JSONResponse([])
    files = sorted(f for f in os.listdir(rendered_dir) if f.endswith(".png"))
    return JSONResponse([{"index": i, "filename": f} for i, f in enumerate(files)])


@app.get("/api/projects/{project_id}/assets/slides/{idx}")
async def api_get_slide_image(project_id: str, idx: int):
    work = project_dir(project_id)
    rendered_dir = os.path.join(work, "rendered_slides")
    fname = f"slide_{idx:03d}.png"
    path = os.path.join(rendered_dir, fname)
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png")


@app.get("/api/projects/{project_id}/assets/slides/{idx}/html")
async def api_get_slide_html(project_id: str, idx: int):
    work = project_dir(project_id)
    html_dir = os.path.join(work, "rendered_slides", "html_debug")
    fname = f"slide_{idx:03d}.html"
    path = os.path.join(html_dir, fname)
    if not os.path.isfile(path):
        raise HTTPException(404)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return JSONResponse({"index": idx, "html": content})


@app.put("/api/projects/{project_id}/assets/slides/{idx}/html")
async def api_update_slide_html(project_id: str, idx: int, request: Request):
    work = project_dir(project_id)
    html_dir = os.path.join(work, "rendered_slides", "html_debug")
    fname = f"slide_{idx:03d}.html"
    path = os.path.join(html_dir, fname)
    if not os.path.isfile(path):
        raise HTTPException(404)
    body = await request.json()
    html = body.get("html", "")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return JSONResponse({"ok": True})


@app.get("/api/projects/{project_id}/assets/plan")
async def api_get_plan(project_id: str):
    work = project_dir(project_id)
    path = os.path.join(work, "slide_plan.json")
    if not os.path.isfile(path):
        raise HTTPException(404)
    with open(path, encoding="utf-8") as f:
        return JSONResponse(json.load(f))


@app.put("/api/projects/{project_id}/assets/plan")
async def api_update_plan(project_id: str, request: Request):
    work = project_dir(project_id)
    path = os.path.join(work, "slide_plan.json")
    body = await request.json()
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(body, indent=2, ensure_ascii=False))
    return JSONResponse({"ok": True})


@app.get("/api/projects/{project_id}/assets/raw-response")
async def api_get_raw_response(project_id: str):
    work = project_dir(project_id)
    path = os.path.join(work, "openai_raw_response.json")
    if not os.path.isfile(path):
        raise HTTPException(404)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return JSONResponse({"raw": content})


@app.get("/api/projects/{project_id}/download")
async def api_download_pptx(project_id: str):
    meta = _get_project_or_404(project_id)
    work = project_dir(project_id)
    path = os.path.join(work, "output.pptx")
    if not os.path.isfile(path):
        raise HTTPException(404, "PPTX not found")
    filename = f"{meta.name}.pptx".replace(" ", "_")
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", filename=filename)


@app.get("/api/projects/{project_id}/assets/video")
async def api_get_video(project_id: str):
    work = project_dir(project_id)
    path = os.path.join(work, "video.mp4")
    if not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


# ---- main ----------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
