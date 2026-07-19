from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import logging
from pathlib import Path
from typing import Optional
import aiofiles
import uuid
import json
import re
import openai

if __package__:
    from .video_processor import VideoProcessor
    from .transcriber import Transcriber
    from .summarizer import Summarizer
    from .translator import Translator
    from .logging_safety import disable_sensitive_dependency_logs, log_exception
    from .model_settings import validate_temperature
    from .v2.bootstrap import install_v2
else:
    from video_processor import VideoProcessor
    from transcriber import Transcriber
    from summarizer import Summarizer
    from translator import Translator
    from logging_safety import disable_sensitive_dependency_logs, log_exception
    from model_settings import validate_temperature
    from v2.bootstrap import install_v2

# 配置日志
logging.basicConfig(level=logging.INFO)
disable_sensitive_dependency_logs()
logger = logging.getLogger(__name__)

app = FastAPI(title="AI视频转录器", version="1.0.0")

# CORS中间件配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

v2_runtime = install_v2(app, PROJECT_ROOT)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")

# 创建临时目录
TEMP_DIR = PROJECT_ROOT / "temp"
TEMP_DIR.mkdir(exist_ok=True)

# 初始化处理器
video_processor = VideoProcessor()
transcriber = Transcriber()
summarizer = Summarizer()
translator = Translator()

# 存储任务状态 - 使用文件持久化
import threading

TASKS_FILE = TEMP_DIR / "tasks.json"
tasks_lock = threading.Lock()

def load_tasks():
    """加载任务状态"""
    try:
        if TASKS_FILE.exists():
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_tasks(tasks_data):
    """保存任务状态"""
    try:
        with tasks_lock:
            with open(TASKS_FILE, 'w', encoding='utf-8') as f:
                json.dump(tasks_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_exception(logger, logging.ERROR, "保存任务状态失败", e)

async def broadcast_task_update(task_id: str, task_data: dict):
    """向所有连接的SSE客户端广播任务状态更新"""
    logger.info(f"广播任务更新: {task_id}, 状态: {task_data.get('status')}, 连接数: {len(sse_connections.get(task_id, []))}")
    if task_id in sse_connections:
        connections_to_remove = []
        for queue in sse_connections[task_id]:
            try:
                await queue.put(json.dumps(task_data, ensure_ascii=False))
                logger.debug(f"消息已发送到队列: {task_id}")
            except Exception as e:
                log_exception(logger, logging.WARNING, "发送消息到队列失败", e)
                connections_to_remove.append(queue)
        
        # 移除断开的连接
        for queue in connections_to_remove:
            sse_connections[task_id].remove(queue)
        
        # 如果没有连接了，清理该任务的连接列表
        if not sse_connections[task_id]:
            del sse_connections[task_id]

# 启动时加载任务状态
tasks = load_tasks()
# 存储正在处理的URL，防止重复处理
processing_urls = set()
# 存储活跃的任务对象，用于控制和取消
active_tasks = {}
# 存储SSE连接，用于实时推送状态更新
sse_connections = {}

# 本地上传：允许的类型与大小上限（MB），可用环境变量 UPLOAD_MAX_MB 调整
UPLOAD_ALLOWED_EXT = frozenset({".txt", ".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mkv", ".ogg", ".flac"})
UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "200"))


def _temperature_or_400(value) -> float:
    try:
        return validate_temperature(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _create_request_ai_services(
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    temperature: float = 0.1,
):
    validated_temperature = validate_temperature(temperature)
    kwargs = {
        "api_key": (api_key or "").strip() or None,
        "base_url": (model_base_url or "").strip().rstrip("/") or None,
        "model": (model_id or "").strip() or None,
        "temperature": validated_temperature,
    }
    return Summarizer(**kwargs), Translator(**kwargs)


def _sanitize_title_for_filename(title: str) -> str:
    """将视频标题清洗为安全的文件名片段。"""
    if not title:
        return "untitled"
    # 仅保留字母数字、下划线、连字符与空格
    safe = re.sub(r"[^\w\-\s]", "", title)
    # 压缩空白并转为下划线
    safe = re.sub(r"\s+", "_", safe).strip("._-")
    # 最长限制，避免过长文件名问题
    return safe[:80] or "untitled"


def _sanitize_model_for_filename(model_id: str) -> str:
    """将模型名清洗为安全的文件名片段（如 openai/gpt-4o → gpt-4o）。"""
    if not model_id:
        return "default"
    # 去掉供应商前缀，只保留最后一段
    tail = str(model_id).strip().split("/")[-1]
    safe = re.sub(r"[^\w\-\.]", "", tail).strip("._-")
    return safe[:40] or "default"


def _output_dir_for_task(safe_title: str, short_id: str) -> Path:
    """按标题+任务短ID创建输出目录并返回路径。"""
    out_dir = TEMP_DIR / f"{safe_title}_{short_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _txt_to_raw_transcript_markdown(body: str) -> str:
    """将纯文本包装为与 Whisper 输出结构一致的 Markdown。"""
    text = body.strip() if body.strip() else "(empty)"
    return "\n".join([
        "# Video Transcription",
        "",
        "**Detected Language:**",
        "**Language Probability:** —",
        "",
        "## Transcription Content",
        "",
        text,
    ])


async def _run_post_extract_pipeline(
    task_id: str,
    raw_script: str,
    video_title: str,
    source_ref: str,
    summary_language: str,
    request_summarizer: Summarizer,
    request_translator: Translator,
    dedup_url: Optional[str] = None,
    model_id: str = "",
) -> None:
    """取得 raw_script 后的共用管线：归档、优化、翻译、摘要、广播。"""
    short_id = task_id.replace("-", "")[:6]
    safe_title = _sanitize_title_for_filename(video_title)
    model_slug = _sanitize_model_for_filename(model_id)
    out_dir = _output_dir_for_task(safe_title, short_id)
    folder_name = out_dir.name

    try:
        raw_md_filename = f"raw_{model_slug}.md"
        raw_md_path = out_dir / raw_md_filename
        with open(raw_md_path, "w", encoding="utf-8") as f:
            f.write((raw_script or "") + f"\n\nsource: {source_ref}\n")
        tasks[task_id].update({
            "raw_script_file": f"{folder_name}/{raw_md_filename}",
            "raw_filename": raw_md_filename,
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
    except Exception as e:
        log_exception(logger, logging.ERROR, "保存原始转录Markdown失败", e)

    tasks[task_id].update({
        "progress": 55,
        "message": "正在优化转录文本...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    script = await request_summarizer.optimize_transcript(raw_script)

    script_with_title = f"# {video_title}\n\n{script}\n\nsource: {source_ref}\n"

    detected_language = transcriber.get_detected_language(raw_script)
    detected_language = (detected_language or "").strip()
    if not detected_language:
        detected_language = request_translator.infer_language_code(raw_script)
    detected_language = request_translator.normalize_lang_code(detected_language) or detected_language

    logger.info("已检测源语言并选择摘要语言")

    translation_content = None
    translation_filename = None
    translation_path = None

    need_translation = request_translator.languages_differ_for_translation(
        detected_language, summary_language
    )

    if need_translation:
        logger.info("源语言与摘要语言不同，开始翻译")
        tasks[task_id].update({
            "progress": 70,
            "message": "正在生成翻译...",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        translation_content = await request_translator.translate_text(
            script, summary_language, detected_language
        )
        translation_with_title = f"# {video_title}\n\n{translation_content}\n\nsource: {source_ref}\n"
        translation_filename = f"translation_{model_slug}.md"
        translation_path = out_dir / translation_filename
        async with aiofiles.open(translation_path, "w", encoding="utf-8") as f:
            await f.write(translation_with_title)
    else:
        logger.info(
            "不需要翻译: need_translation=%s", need_translation
        )

    tasks[task_id].update({
        "progress": 80,
        "message": "正在生成摘要...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    summary = await request_summarizer.summarize(script, summary_language, video_title)
    summary_with_source = summary + f"\n\nsource: {source_ref}\n"

    script_filename = f"transcript_{model_slug}.md"
    script_path = out_dir / script_filename
    async with aiofiles.open(script_path, "w", encoding="utf-8") as f:
        await f.write(script_with_title)

    summary_filename = f"summary_{model_slug}.md"
    summary_path = out_dir / summary_filename
    async with aiofiles.open(summary_path, "w", encoding="utf-8") as f:
        await f.write(summary_with_source)

    task_result = {
        "status": "completed",
        "progress": 100,
        "message": "处理完成！",
        "video_title": video_title,
        "script": script_with_title,
        "summary": summary_with_source,
        "script_path": str(script_path),
        "summary_path": str(summary_path),
        "script_filename": script_filename,
        "summary_filename": summary_filename,
        "output_folder": folder_name,
        "model_id": model_id or "",
        "model_slug": model_slug,
        "short_id": short_id,
        "safe_title": safe_title,
        "detected_language": detected_language,
        "summary_language": summary_language,
    }

    if translation_content and translation_path:
        task_result.update({
            "translation": translation_with_title,
            "translation_path": str(translation_path),
            "translation_filename": translation_filename,
        })

    tasks[task_id].update(task_result)
    save_tasks(tasks)
    logger.info(f"任务完成，准备广播最终状态: {task_id}")
    await broadcast_task_update(task_id, tasks[task_id])
    logger.info(f"最终状态已广播: {task_id}")

    if dedup_url:
        processing_urls.discard(dedup_url)
    if task_id in active_tasks:
        del active_tasks[task_id]


@app.get("/")
async def read_root():
    """返回前端页面"""
    return FileResponse(str(PROJECT_ROOT / "static" / "index.html"))

@app.post("/api/models")
async def list_models(
    base_url: str = Form(default=""),
    api_key:  str = Form(default=""),
):
    """Proxy: fetch model list from any OpenAI-compatible API."""
    effective_key = api_key or os.getenv("OPENAI_API_KEY", "")
    effective_url = base_url.rstrip("/") or os.getenv("OPENAI_BASE_URL") or None

    if not effective_key:
        raise HTTPException(status_code=400, detail="API key is required")

    try:
        client = openai.OpenAI(api_key=effective_key, base_url=effective_url)
        resp   = await asyncio.to_thread(client.models.list)
        models = [{"id": m.id, "name": getattr(m, "name", m.id)} for m in resp.data]
        # Sort by id for readability
        models.sort(key=lambda x: x["id"])
        return {"data": models}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _enqueue_upload_job(
    file: UploadFile,
    summary_language: str,
    api_key: str,
    model_base_url: str,
    model_id: str,
    temperature: float,
) -> dict:
    """保存上传文件并入队 process_upload_task，返回 {task_id, message}。"""
    raw_name = file.filename or "upload.bin"
    if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe_name = os.path.basename(raw_name)
    ext = Path(safe_name).suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or '(none)'}",
        )

    max_bytes = UPLOAD_MAX_MB * 1024 * 1024
    task_id = str(uuid.uuid4())
    unique_stem = task_id.replace("-", "")[:12]
    dest = TEMP_DIR / f"upload_{unique_stem}{ext}"

    total = 0
    with open(dest, "wb") as out_f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds limit of {UPLOAD_MAX_MB} MB",
                )
            out_f.write(chunk)

    if total == 0:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Empty file")

    video_title = _sanitize_title_for_filename(Path(safe_name).stem) or "upload"
    source_label = f"upload:{safe_name}"

    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": "开始处理上传文件...",
        "script": None,
        "summary": None,
        "error": None,
        "url": source_label,
    }
    save_tasks(tasks)

    bg = asyncio.create_task(
        process_upload_task(
            task_id,
            dest,
            safe_name,
            video_title,
            ext,
            summary_language,
            api_key,
            model_base_url,
            model_id,
            temperature,
        )
    )
    active_tasks[task_id] = bg

    return {"task_id": task_id, "message": "任务已创建，正在处理中..."}


@app.post("/api/process-video")
async def process_video(
    url: str = Form(default=""),
    summary_language: str = Form(default="zh"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    temperature: str = Form(default="0.1"),
    file: Optional[UploadFile] = File(None),
):
    """
    处理视频链接或本地上传（multipart 中带 file 且无有效 URL 时走上传流程）。
    上传与 URL 共用此路径，便于反向代理只放行 /api/process-video 的环境。
    """
    try:
        validated_temperature = _temperature_or_400(temperature)
        if file is not None and (file.filename or "").strip():
            return await _enqueue_upload_job(
                file,
                summary_language,
                api_key,
                model_base_url,
                model_id,
                validated_temperature,
            )

        stripped = (url or "").strip()
        if not stripped:
            raise HTTPException(
                status_code=400,
                detail="Provide a video URL or upload a file",
            )

        url = stripped

        # 检查是否已经在处理相同的URL
        if url in processing_urls:
            # 查找现有任务
            for tid, task in tasks.items():
                if task.get("url") == url:
                    return {"task_id": tid, "message": "该视频正在处理中，请等待..."}
            
        # 生成唯一任务ID
        task_id = str(uuid.uuid4())
        
        # 标记URL为正在处理
        processing_urls.add(url)
        
        # 初始化任务状态
        tasks[task_id] = {
            "status": "processing",
            "progress": 0,
            "message": "开始处理视频...",
            "script": None,
            "summary": None,
            "error": None,
            "url": url  # 保存URL用于去重
        }
        save_tasks(tasks)
        
        # 创建并跟踪异步任务
        task = asyncio.create_task(
            process_video_task(
                task_id,
                url,
                summary_language,
                api_key,
                model_base_url,
                model_id,
                validated_temperature,
            )
        )
        active_tasks[task_id] = task
        
        return {"task_id": task_id, "message": "任务已创建，正在处理中..."}
        
    except HTTPException:
        raise
    except Exception as e:
        log_exception(logger, logging.ERROR, "处理视频时出错", e)
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

async def process_video_task(
    task_id: str,
    url: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    temperature: float = 0.1,
):
    """
    异步处理视频任务
    """
    try:
        # ── 阶段一：优先尝试获取平台字幕（快速路径） ──────────────────────
        tasks[task_id].update({
            "status": "processing",
            "progress": 10,
            "message": "正在检测视频字幕..."
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
        await asyncio.sleep(0.1)

        request_summarizer, request_translator = _create_request_ai_services(
            api_key,
            model_base_url,
            model_id,
            temperature,
        )
        logger.info(
            "Created request-scoped AI services: custom_endpoint=%s, "
            "model_configured=%s, temperature=%s",
            bool(model_base_url.rstrip("/")),
            bool(model_id),
            temperature,
        )

        subtitle_text, sub_title, sub_lang = await video_processor.fetch_subtitles(url, TEMP_DIR)

        if subtitle_text:
            # ── 快速路径：有字幕，跳过音频下载和 Whisper ──────────────────
            video_title = sub_title
            raw_script = subtitle_text
            # 把语言写入 transcriber，保持下游逻辑一致
            transcriber.last_detected_language = sub_lang

            tasks[task_id].update({
                "progress": 40,
                "message": f"字幕获取成功（{sub_lang}），正在处理文本..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])
        else:
            # ── 慢速路径：无字幕，下载音频 → Whisper 转录 ─────────────────
            tasks[task_id].update({
                "progress": 15,
                "message": "未找到字幕，正在下载视频音频..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            audio_path, video_title = await video_processor.download_and_convert(
                url, TEMP_DIR, prefetched_title=sub_title or None
            )

            tasks[task_id].update({
                "progress": 35,
                "message": "音频下载完成，准备转录..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            tasks[task_id].update({
                "progress": 40,
                "message": "正在转录音频（Whisper）..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            raw_script = await transcriber.transcribe(audio_path)

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=url,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            request_translator=request_translator,
            dedup_url=url,
            model_id=model_id,
        )

        # 不要立即删除临时文件！保留给用户下载
        # 文件会在一定时间后自动清理或用户手动清理

    except Exception as e:
        log_exception(logger, logging.ERROR, f"任务 {task_id} 处理失败", e)
        # 从处理列表中移除URL
        processing_urls.discard(url)
        
        # 从活跃任务列表中移除
        if task_id in active_tasks:
            del active_tasks[task_id]
            
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"处理失败: {str(e)}"
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

@app.post("/api/process-upload")
async def process_upload(
    file: UploadFile = File(...),
    summary_language: str = Form(default="zh"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    temperature: str = Form(default="0.1"),
):
    """独立上传入口；逻辑与 multipart 带 file 的 /api/process-video 相同。"""
    return await _enqueue_upload_job(
        file,
        summary_language,
        api_key,
        model_base_url,
        model_id,
        _temperature_or_400(temperature),
    )


async def process_upload_task(
    task_id: str,
    saved_path: Path,
    original_name: str,
    video_title: str,
    ext_lower: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    temperature: float = 0.1,
):
    source_ref = f"upload:{original_name}"
    try:
        request_summarizer, request_translator = _create_request_ai_services(
            api_key,
            model_base_url,
            model_id,
            temperature,
        )
        logger.info(
            "Created upload AI services: custom_endpoint=%s, "
            "model_configured=%s, temperature=%s",
            bool(model_base_url.rstrip("/")),
            bool(model_id),
            temperature,
        )

        if ext_lower == ".txt":
            tasks[task_id].update({
                "progress": 20,
                "message": "正在读取文本文件...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            body = saved_path.read_text(encoding="utf-8", errors="replace")
            if not body.strip():
                raise Exception("文本文件为空")
            transcriber.last_detected_language = None
            raw_script = _txt_to_raw_transcript_markdown(body)
        else:
            tasks[task_id].update({
                "progress": 15,
                "message": "正在转换音频格式...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            audio_path = await video_processor.normalize_local_media_to_m4a(saved_path, TEMP_DIR)

            tasks[task_id].update({
                "progress": 35,
                "message": "音频准备完成，准备转录...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            tasks[task_id].update({
                "progress": 40,
                "message": "正在转录音频（Whisper）...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            raw_script = await transcriber.transcribe(audio_path)

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=source_ref,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            request_translator=request_translator,
            dedup_url=None,
            model_id=model_id,
        )

    except Exception as e:
        log_exception(logger, logging.ERROR, f"任务 {task_id} 处理失败", e)
        if task_id in active_tasks:
            del active_tasks[task_id]
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"处理失败: {str(e)}",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])


@app.get("/api/task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    获取任务状态
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return tasks[task_id]

@app.get("/api/task-stream/{task_id}")
async def task_stream(task_id: str):
    """
    SSE实时任务状态流
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    async def event_generator():
        # 创建任务专用的队列
        queue = asyncio.Queue()
        
        # 将队列添加到连接列表
        if task_id not in sse_connections:
            sse_connections[task_id] = []
        sse_connections[task_id].append(queue)
        
        try:
            # 立即发送当前状态
            current_task = tasks.get(task_id, {})
            yield f"data: {json.dumps(current_task, ensure_ascii=False)}\n\n"
            
            # 持续监听状态更新
            while True:
                try:
                    # 等待状态更新，超时时间30秒发送心跳
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                    
                    # 如果任务完成或失败，结束流
                    task_data = json.loads(data)
                    if task_data.get("status") in ["completed", "error"]:
                        break
                        
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                    
        except asyncio.CancelledError:
            logger.info(f"SSE连接被取消: {task_id}")
        except Exception as e:
            log_exception(logger, logging.ERROR, "SSE流异常", e)
        finally:
            # 清理连接
            if task_id in sse_connections and queue in sse_connections[task_id]:
                sse_connections[task_id].remove(queue)
                if not sse_connections[task_id]:
                    del sse_connections[task_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )

@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """
    直接从temp目录下载文件（简化方案）
    """
    try:
        # 检查文件扩展名安全性
        if not filename.endswith('.md'):
            raise HTTPException(status_code=400, detail="仅支持下载.md文件")
        
        # 检查文件名格式（防止路径遍历攻击）
        if '..' in filename or '/' in filename or '\\' in filename:
            raise HTTPException(status_code=400, detail="文件名格式无效")
            
        file_path = TEMP_DIR / filename
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")
            
        return FileResponse(
            file_path,
            filename=filename,
            media_type="text/markdown"
        )
    except HTTPException:
        raise
    except Exception as e:
        log_exception(logger, logging.ERROR, "下载文件失败", e)
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")


@app.get("/api/download/{folder}/{filename}")
async def download_file_in_folder(folder: str, filename: str):
    """从按标题分组的子目录下载文件"""
    try:
        if not filename.endswith('.md'):
            raise HTTPException(status_code=400, detail="仅支持下载.md文件")
        for part in (folder, filename):
            if '..' in part or '/' in part or '\\' in part:
                raise HTTPException(status_code=400, detail="文件名格式无效")

        file_path = (TEMP_DIR / folder / filename).resolve()
        if TEMP_DIR.resolve() not in file_path.parents:
            raise HTTPException(status_code=400, detail="路径无效")
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")

        return FileResponse(
            file_path,
            filename=f"{folder}_{filename}",
            media_type="text/markdown"
        )
    except HTTPException:
        raise
    except Exception as e:
        log_exception(logger, logging.ERROR, "下载文件失败", e)
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")


# ── 生成文件库（Library）API ────────────────────────────────

_FILE_KINDS = ("raw", "transcript", "translation", "summary")
_ROOT_GROUP = "_root"


def _parse_md_filename(name: str) -> dict:
    """解析 {kind}_{model}.md 或旧版 {kind}_{title}_{id}.md 文件名。"""
    stem = name[:-3] if name.endswith(".md") else name
    parts = stem.split("_", 1)
    kind = parts[0] if parts[0] in _FILE_KINDS else "other"
    model = parts[1] if len(parts) > 1 and parts[0] in _FILE_KINDS else ""
    return {"kind": kind, "model": model}


def _group_display_title(folder_name: str) -> str:
    """从 {safe_title}_{short_id} 目录名提取展示标题。"""
    stem = re.sub(r"_[0-9a-f]{6}$", "", folder_name)
    return stem.replace("_", " ").strip() or folder_name


def _file_entry(path: Path, include_model: bool = True) -> dict:
    stat = path.stat()
    meta = _parse_md_filename(path.name)
    return {
        "name": path.name,
        "kind": meta["kind"],
        "model": meta["model"] if include_model else "",
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def _resolve_in_temp(folder: str, filename: Optional[str] = None) -> Path:
    """安全解析 temp 内路径，阻止路径遍历。"""
    for part in [p for p in (folder, filename) if p]:
        if '..' in part or '/' in part or '\\' in part:
            raise HTTPException(status_code=400, detail="路径格式无效")
    base = TEMP_DIR.resolve()
    target = (base / ("" if folder == _ROOT_GROUP else folder) / (filename or "")).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(status_code=400, detail="路径无效")
    return target


@app.get("/api/files")
async def list_generated_files():
    """列出 temp 下所有生成的 Markdown 文件，按标题目录分组。"""
    groups = []
    try:
        # 子目录分组（新版结构：{safe_title}_{short_id}/xxx.md）
        for d in sorted(TEMP_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not d.is_dir():
                continue
            md_files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not md_files:
                continue
            groups.append({
                "folder": d.name,
                "title": _group_display_title(d.name),
                "mtime": max(p.stat().st_mtime for p in md_files),
                "files": [_file_entry(p) for p in md_files],
            })

        # 根目录遗留散文件（旧版结构，文件名不含模型信息）
        root_files = sorted(TEMP_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if root_files:
            groups.append({
                "folder": _ROOT_GROUP,
                "title": "",
                "mtime": max(p.stat().st_mtime for p in root_files),
                "files": [_file_entry(p, include_model=False) for p in root_files],
            })
    except Exception as e:
        log_exception(logger, logging.ERROR, "列出文件失败", e)
        raise HTTPException(status_code=500, detail=f"列出文件失败: {str(e)}")

    return {"groups": groups}


@app.get("/api/files/{folder}/{filename}")
async def read_generated_file(folder: str, filename: str):
    """读取单个生成文件的内容用于预览。"""
    if not filename.endswith('.md'):
        raise HTTPException(status_code=400, detail="仅支持预览.md文件")
    file_path = _resolve_in_temp(folder, filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if file_path.stat().st_size > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，无法预览")
    content = file_path.read_text(encoding="utf-8", errors="replace")
    entry = _file_entry(file_path)
    entry["content"] = content
    return entry


@app.delete("/api/files/{folder}/{filename}")
async def delete_generated_file(folder: str, filename: str):
    """删除单个生成文件；目录清空后顺带删除目录。"""
    file_path = _resolve_in_temp(folder, filename)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    try:
        file_path.unlink()
        parent = file_path.parent
        if parent != TEMP_DIR.resolve() and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
        return {"message": "文件已删除"}
    except Exception as e:
        log_exception(logger, logging.ERROR, "删除文件失败", e)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.delete("/api/files/{folder}")
async def delete_generated_folder(folder: str):
    """删除整个分组目录及其中的 Markdown 文件。"""
    if folder == _ROOT_GROUP:
        raise HTTPException(status_code=400, detail="根目录分组不支持整体删除")
    dir_path = _resolve_in_temp(folder)
    if not dir_path.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")
    try:
        import shutil
        shutil.rmtree(dir_path)
        return {"message": "目录已删除"}
    except Exception as e:
        log_exception(logger, logging.ERROR, "删除目录失败", e)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str):
    """
    取消并删除任务
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 如果任务还在运行，先取消它
    if task_id in active_tasks:
        task = active_tasks[task_id]
        if not task.done():
            task.cancel()
            logger.info(f"任务 {task_id} 已被取消")
        del active_tasks[task_id]
    
    # 从处理URL列表中移除
    task_url = tasks[task_id].get("url")
    if task_url:
        processing_urls.discard(task_url)
    
    # 删除任务记录
    del tasks[task_id]
    return {"message": "任务已取消并删除"}

@app.get("/api/tasks/active")
async def get_active_tasks():
    """
    获取当前活跃任务列表（用于调试）
    """
    active_count = len(active_tasks)
    processing_count = len(processing_urls)
    return {
        "active_tasks": active_count,
        "processing_urls": processing_count,
        "task_ids": list(active_tasks.keys())
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
