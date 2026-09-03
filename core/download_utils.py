import os
import re
import subprocess
import threading
import time

from core.download_types import build_result


SELECTED_FORMAT_LOG_PREFIX = "[FORMAT]"
OUTPUT_FILE_LOG_PREFIX = "[VIDOON_OUTPUT]"
OUTPUT_ID_LOG_PREFIX = "[VIDOON_OUTPUT_ID]"
FINAL_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
TEMP_FILE_EXTENSIONS = {".part", ".ytdl", ".temp", ".tmp"}
TOOL_UPDATE_URLS = {
    "yt-dlp": "https://github.com/yt-dlp/yt-dlp/releases/latest",
    "FFmpeg": "https://ffmpeg.org/download.html",
    "Deno": "https://github.com/denoland/deno/releases/latest",
    "BgUtils PO Token Provider": "https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/latest",
}
DOWNLOAD_PROGRESS_TEMPLATE = (
    "[download] %(progress._percent_str)s of %(progress._total_bytes_str)s "
    "at %(progress._speed_str)s ETA %(progress._eta_str)s"
)

STRATEGY_LABELS = {
    "START": "开始准备下载",
    "PlatformNoCookie": "平台专属无 Cookie 下载",
    "PlatformCookie": "平台专属 Cookie 下载",
    "PlatformBrowserCookie": "平台专属浏览器 Cookie 下载",
    "YouTubeAdvancedAuth": "YouTube Cookie + 高级验证参数",
    "YouTubePoToken": "YouTube 本地 PO Token 验证通道",
    "YouTubeFormatFallback": "YouTube 降级格式重试",
    "YouTubeHlsFallback": "YouTube HLS 备用通道",
    "DenoFallback": "Deno 解析兜底下载",
    "UNKNOWN": "未知下载策略",
}

STRATEGY_LOG_NAMES = {
    "PlatformNoCookie": "策略1",
    "PlatformCookie": "策略2",
    "PlatformBrowserCookie": "策略2",
    "AdvancedAuth": "策略3",
    "YouTubeAdvancedAuth": "策略3",
    "YouTubePoToken": "策略4",
    "FormatFallback": "策略4",
    "YouTubeFormatFallback": "策略5",
    "YouTubeHlsFallback": "策略5",
    "DenoFallback": "策略5",
}

DEFAULT_ERROR_MESSAGES = {
    "NETWORK_RATE_LIMIT": "请求太频繁了，平台暂时限流了，请稍后再试。",
    "YOUTUBE_HTTP_403": "YouTube 拒绝了当前媒体下载通道（HTTP 403）。",
    "DOWNLOAD_RANGE_INVALID": "本地残留下载文件和服务器返回范围不一致，请重新下载这条视频。",
    "AUTH_BOT_CHECK": "平台触发了机器人验证，当前账号或 IP 环境被限制，请稍后再试或更换网络。",
    "AUTH_COOKIE_INVALID": "Cookie 已失效，需要重新导入最新 Cookie。",
    "AUTH_NEED_LOGIN": "这个内容需要登录后才能下载。",
    "CONTENT_AGE_RESTRICTED": "这个内容有年龄限制，需要登录账号后才能下载。",
    "PRIVATE_VIDEO": "这个内容是私密内容，需要有权限的账号才能下载。",
    "REGION_RESTRICTED": "这个内容有地区限制，当前网络地区可能无法访问。",
    "NETWORK_ERROR": "网络连接异常，请检查网络或 VPN 后重试。",
    "NETWORK_TIMEOUT": "请求超时了，请稍后重试。",
    "EXTRACTOR_FAILED": "平台页面结构变了，常规解析失败，准备尝试 Deno 兜底。",
    "INVALID_URL": "链接格式不对，暂时无法下载。",
    "INVALID_PLATFORM": "当前平台暂不支持下载。",
    "STOPPED": "下载已停止。",
    "OUTPUT_NOT_FOUND": "下载进程已经结束，但没有检测到本次任务生成的完整视频文件。",
    "UNKNOWN": "下载失败，原因暂时无法明确识别。",
}

PLATFORM_ERROR_MESSAGES = {
    "YouTube": {
        "NETWORK_RATE_LIMIT": "YouTube 当前限制了这次请求，像是访问太频繁了，请稍后再试或换个网络环境。",
        "YOUTUBE_HTTP_403": "YouTube 拒绝了当前媒体下载通道（HTTP 403），工具已自动尝试备用通道但仍未成功。",
        "DOWNLOAD_RANGE_INVALID": "这条 YouTube 视频的本地残留下载文件和服务器范围不一致，请重新下载这条视频。",
        "AUTH_BOT_CHECK": "YouTube 触发机器人验证，当前账号或 IP 环境被限制。工具已完成自动增强重试，请更换网络、降低频率，或重新导入 Cookie。",
        "AUTH_COOKIE_INVALID": "这份 YouTube Cookie 已失效，需要重新导出并导入。",
        "AUTH_NEED_LOGIN": "这个 YouTube 视频需要登录后才能下载，请导入可用的 YouTube Cookie。",
        "CONTENT_AGE_RESTRICTED": "这个 YouTube 视频有年龄限制，请导入已登录成年账号的 YouTube Cookie。",
        "PRIVATE_VIDEO": "这个 YouTube 视频是私密或受限内容，当前账号没有权限访问。",
        "NETWORK_ERROR": "连接 YouTube 失败了，请检查网络、代理或 VPN 后重试。",
        "NETWORK_TIMEOUT": "连接 YouTube 超时了，请稍后重试。",
        "EXTRACTOR_FAILED": "YouTube 页面结构可能变了，常规解析失败，准备尝试其他验证通道。",
    },
    "TikTok": {
        "NETWORK_RATE_LIMIT": "TikTok 当前限制了请求频率，请稍后再试，别连续点太快。",
        "DOWNLOAD_RANGE_INVALID": "这条 TikTok 视频的本地残留下载文件和服务器范围不一致，请重新下载这条视频。",
        "AUTH_COOKIE_INVALID": "这份 TikTok Cookie 已失效，需要重新导出并导入。",
        "AUTH_NEED_LOGIN": "这个 TikTok 内容需要登录状态才能访问，请导入可用的 TikTok Cookie。",
        "REGION_RESTRICTED": "这个 TikTok 内容可能有地区限制，当前网络地区暂时无法访问。",
        "NETWORK_ERROR": "连接 TikTok 失败了，请检查网络或代理环境后重试。",
        "NETWORK_TIMEOUT": "连接 TikTok 超时了，请稍后重试。",
        "EXTRACTOR_FAILED": "TikTok 页面解析失败了，准备尝试 Deno 兜底。",
    },
    "Instagram": {
        "NETWORK_RATE_LIMIT": "Instagram 当前限制了访问频率，请稍后再试，先别连续请求太多次。",
        "DOWNLOAD_RANGE_INVALID": "这条 Instagram 视频的本地残留下载文件和服务器范围不一致，请重新下载这条视频。",
        "AUTH_COOKIE_INVALID": "这份 Instagram Cookie 已失效，需要重新导出并导入。",
        "AUTH_NEED_LOGIN": "这个 Instagram 内容需要登录后才能下载，请导入可用的 Instagram Cookie。",
        "NETWORK_ERROR": "连接 Instagram 失败了，请检查网络或代理后重试。",
        "NETWORK_TIMEOUT": "连接 Instagram 超时了，请稍后重试。",
        "EXTRACTOR_FAILED": "Instagram 页面结构可能变了，常规解析失败，准备尝试 Deno 兜底。",
    },
}


def build_selected_format_print_template() -> str:
    return (
        f"before_dl:{SELECTED_FORMAT_LOG_PREFIX} "
        "id=%(format_id)s ext=%(ext)s res=%(resolution)s fps=%(fps)s tbr=%(tbr)s"
    )


def build_output_file_print_template() -> str:
    return f"after_move:{OUTPUT_FILE_LOG_PREFIX} %(filepath)s"


def build_output_id_print_template() -> str:
    return f"after_move:{OUTPUT_ID_LOG_PREFIX} %(id)s"


def extract_output_file_paths(output_text: str) -> list[str]:
    paths = []
    seen = set()
    for line in (output_text or "").splitlines():
        marker_index = line.find(OUTPUT_FILE_LOG_PREFIX)
        if marker_index < 0:
            continue
        path = line[marker_index + len(OUTPUT_FILE_LOG_PREFIX):].strip().strip('"')
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def extract_output_ids(output_text: str) -> list[str]:
    output_ids = []
    seen = set()
    for line in (output_text or "").splitlines():
        marker_index = line.find(OUTPUT_ID_LOG_PREFIX)
        if marker_index < 0:
            continue
        output_id = line[marker_index + len(OUTPUT_ID_LOG_PREFIX):].strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{3,128}", output_id) or output_id in seen:
            continue
        seen.add(output_id)
        output_ids.append(output_id)
    return output_ids


def validate_output_files(
    paths: list[str],
    *,
    output_ids: list[str] | None = None,
    search_dir: str = "",
) -> list[str]:
    valid_paths = []
    seen_paths = set()
    for path in paths or []:
        normalized_path = os.path.abspath(os.path.expanduser(path))
        extension = os.path.splitext(normalized_path)[1].lower()
        if extension in TEMP_FILE_EXTENSIONS or extension not in FINAL_VIDEO_EXTENSIONS:
            continue
        try:
            if os.path.isfile(normalized_path) and os.path.getsize(normalized_path) > 0:
                valid_paths.append(normalized_path)
                seen_paths.add(os.path.normcase(normalized_path))
        except OSError:
            continue

    if output_ids and search_dir and os.path.isdir(search_dir):
        suffixes = tuple(f"-{output_id}{extension}" for output_id in output_ids for extension in FINAL_VIDEO_EXTENSIONS)
        try:
            for entry in os.scandir(search_dir):
                if not entry.is_file() or not entry.name.endswith(suffixes):
                    continue
                normalized_path = os.path.abspath(entry.path)
                normalized_key = os.path.normcase(normalized_path)
                if normalized_key in seen_paths or entry.stat().st_size <= 0:
                    continue
                valid_paths.append(normalized_path)
                seen_paths.add(normalized_key)
        except OSError:
            pass
    return valid_paths


def extract_selected_format_metadata(output_text: str) -> dict | None:
    if not output_text:
        return None

    custom_match = re.search(
        rf"{re.escape(SELECTED_FORMAT_LOG_PREFIX)}\s+id=(?P<format_id>\S+)"
        r"(?:\s+ext=(?P<ext>\S*))?"
        r"(?:\s+res=(?P<resolution>\S*))?"
        r"(?:\s+fps=(?P<fps>\S*))?"
        r"(?:\s+tbr=(?P<tbr>\S*))?",
        output_text,
    )
    if custom_match:
        return {
            "format_id": custom_match.group("format_id") or "",
            "ext": custom_match.group("ext") or "",
            "resolution": custom_match.group("resolution") or "",
            "fps": custom_match.group("fps") or "",
            "tbr": custom_match.group("tbr") or "",
        }

    fallback_match = re.search(r"Downloading \d+ format\(s\): (?P<format_id>[^\r\n]+)", output_text)
    if fallback_match:
        return {
            "format_id": fallback_match.group("format_id").strip(),
            "ext": "",
            "resolution": "",
            "fps": "",
            "tbr": "",
        }

    return None


def format_selected_format_log(selected_format: dict | None) -> str:
    if not selected_format:
        return ""

    parts = [f"format_id={selected_format.get('format_id', 'unknown')}"]
    if selected_format.get("resolution"):
        parts.append(f"res={selected_format['resolution']}")
    if selected_format.get("fps"):
        parts.append(f"fps={selected_format['fps']}")
    if selected_format.get("tbr"):
        parts.append(f"tbr={selected_format['tbr']}")
    if selected_format.get("ext"):
        parts.append(f"ext={selected_format['ext']}")
    return "Selected format: " + ", ".join(parts)


def get_quality_label(selected_format: dict | None) -> str:
    """Return a familiar quality label for either landscape or portrait video."""
    if not selected_format:
        return ""

    resolution = str(selected_format.get("resolution", "") or "").strip()
    dimensions = re.fullmatch(r"(?P<width>\d{2,5})x(?P<height>\d{2,5})", resolution)
    if dimensions:
        width = int(dimensions.group("width"))
        height = int(dimensions.group("height"))
        return f"{min(width, height)}P"

    progressive = re.search(r"(?<!\d)(?P<height>\d{3,4})p(?!\d)", resolution, re.IGNORECASE)
    if not progressive:
        format_id = str(selected_format.get("format_id", "") or "")
        progressive = re.search(r"(?<!\d)(?P<height>\d{3,4})p(?!\d)", format_id, re.IGNORECASE)
    return f"{progressive.group('height')}P" if progressive else ""


def summarize_command_error(raw_stderr: str = "", raw_stdout: str = "", *, max_lines: int = 8, max_chars: int = 1200) -> str:
    text = (raw_stderr or raw_stdout or "").strip()
    if not text:
        return ""

    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""

    important = [
        line for line in lines
        if re.search(r"\b(ERROR|WARNING|HTTP Error|Sign in|bot|429|403|timeout|Unable to)\b", line, re.IGNORECASE)
    ]
    selected_lines = important[-max_lines:] if important else lines[-max_lines:]
    summary = "\n".join(selected_lines)

    if len(summary) > max_chars:
        summary = summary[-max_chars:].lstrip()
        summary = "... " + summary
    return summary


def detect_tool_update_notices(raw_stderr: str = "", raw_stdout: str = "") -> list[str]:
    text = f"{raw_stderr or ''}\n{raw_stdout or ''}"
    lowered = text.lower()
    tools = []

    if (
        ("your yt-dlp version" in lowered and "older than" in lowered)
        or "confirm you are on the latest version using yt-dlp -u" in lowered
        or "please update to nightly" in lowered
    ):
        tools.append("yt-dlp")

    if re.search(
        r"(?:ffmpeg[^\r\n]*(?:too old|outdated|please update|unsupported version)|"
        r"(?:too old|outdated|please update)[^\r\n]*ffmpeg)",
        lowered,
    ):
        tools.append("FFmpeg")

    if re.search(
        r"(?:deno[^\r\n]*(?:too old|outdated|minimum required|unsupported version)|"
        r"(?:too old|outdated|minimum required)[^\r\n]*deno)",
        lowered,
    ):
        tools.append("Deno")

    if (
        ("bgutil" in lowered or "po token provider" in lowered)
        and any(marker in lowered for marker in ("version mismatch", "outdated", "please update"))
    ):
        tools.append("BgUtils PO Token Provider")

    return [
        f"检测到 {tool} 版本过旧或不兼容，请更新后重试。官方下载：{TOOL_UPDATE_URLS[tool]}"
        for tool in tools
    ]


def format_strategy_log(platform_name: str, strategy_name: str, message: str) -> str:
    strategy_label = STRATEGY_LOG_NAMES.get(strategy_name or "", strategy_name or "策略")
    return f"[{strategy_label}][{platform_name}] {message}"


def parse_download_progress(line: str) -> dict | None:
    progress_match = re.search(r"\[download\]\s+([\d.]+)%", line)
    if not progress_match:
        return None

    size_match = re.search(r"\bof\s+~?([^\s]+)", line)
    speed_match = re.search(r"\bat\s+([^\s]+)", line)
    eta_match = re.search(r"\bETA\s+([^\s]+)", line)
    elapsed_match = re.search(r"\bin\s+([^\s]+)", line)
    percent = float(progress_match.group(1))
    total = size_match.group(1) if size_match else ""
    downloaded = _estimate_downloaded_size(percent, total)
    return {
        "percent": percent,
        "size": total,
        "total": total,
        "downloaded": downloaded,
        "speed": speed_match.group(1) if speed_match else "",
        "eta": eta_match.group(1) if eta_match else "",
        "elapsed": elapsed_match.group(1) if elapsed_match else "",
    }


def _estimate_downloaded_size(percent: float, total_text: str) -> str:
    size_match = re.match(r"([\d.]+)([A-Za-z]+)", total_text or "")
    if not size_match:
        return ""

    total_value = float(size_match.group(1))
    unit = size_match.group(2)
    downloaded = total_value * max(0.0, min(100.0, percent)) / 100.0
    return f"{downloaded:.1f}{unit}"


def maybe_log_download_progress(runtime, platform_name: str, strategy_name: str, progress: dict):
    log_callback = getattr(runtime, "log_callback", None)
    if not log_callback:
        return

    now = time.time()
    percent = progress["percent"]
    last_percent = getattr(runtime, "_last_progress_log_percent", -10.0)
    last_time = getattr(runtime, "_last_progress_log_time", 0.0)
    if percent >= 100 and last_percent >= 100 and (now - last_time) < 1.5:
        return
    if percent < 100 and (percent - last_percent) < 2 and (now - last_time) < 1.5:
        return

    runtime._last_progress_log_percent = percent
    runtime._last_progress_log_time = now

    parts = [f"下载进度：{percent:.1f}%"]
    downloaded = progress.get("downloaded")
    total = progress.get("total") or progress.get("size")
    if downloaded and total:
        parts.append(f"{downloaded}/{total}")
    elif total:
        parts.append(f"总大小 {total}")
    if progress.get("speed"):
        parts.append(f"速度 {progress['speed']}")
    if progress.get("eta"):
        parts.append(f"剩余 {progress['eta']}")
    elif progress.get("elapsed"):
        parts.append(f"耗时 {progress['elapsed']}")
    log_callback(format_strategy_log(platform_name, strategy_name, " | ".join(parts)))


def run_logged_download_strategy(
    downloader,
    cmd: list[str],
    url: str,
    log_callback,
    strategy_name: str,
    *,
    cookie_used: bool,
    deno_used: bool = False,
) -> dict:
    runtime = downloader.runtime
    start_time = time.time()
    previous_log_callback = getattr(runtime, "log_callback", None)
    previous_strategy_name = getattr(runtime, "_active_strategy_name", "")
    previous_progress_percent = getattr(runtime, "_last_progress_log_percent", -10.0)
    previous_progress_time = getattr(runtime, "_last_progress_log_time", 0.0)

    runtime.log_callback = log_callback
    runtime._active_strategy_name = strategy_name
    runtime._last_progress_log_percent = -10.0
    runtime._last_progress_log_time = 0.0
    runtime._last_idle_log_time = 0.0

    try:
        log_callback(format_strategy_log(downloader.platform_name, strategy_name, "开始执行 yt-dlp"))

        def log_idle_status():
            now = time.time()
            if now - getattr(runtime, "_last_idle_log_time", 0.0) < 6:
                return
            runtime._last_idle_log_time = now
            log_callback(format_strategy_log(
                downloader.platform_name,
                strategy_name,
                "仍在解析或下载中，等待平台响应...",
            ))

        command_result = run_command(
            cmd,
            progress_parser=downloader._parse_progress,
            idle_callback=log_idle_status,
            cancel_checker=runtime.cancel_checker,
        )
        elapsed = time.time() - start_time
        for update_notice in detect_tool_update_notices(
            command_result.get("raw_stderr", ""),
            command_result.get("raw_stdout", ""),
        ):
            log_callback(format_strategy_log(
                downloader.platform_name,
                strategy_name,
                update_notice,
            ))
        selected_format = command_result.get("selected_format")
        output_files = validate_output_files(
            command_result.get("output_files", []),
            output_ids=command_result.get("output_ids", []),
            search_dir=runtime.save_path,
        )
        if selected_format:
            log_callback(format_strategy_log(downloader.platform_name, strategy_name, format_selected_format_log(selected_format)))

        if output_files:
            if command_result["returncode"] != 0:
                log_callback(format_strategy_log(
                    downloader.platform_name,
                    strategy_name,
                    "下载进程返回异常状态，但已确认本次任务的完整视频文件存在",
                ))
            log_callback(format_strategy_log(downloader.platform_name, strategy_name, f"下载成功，耗时 {elapsed:.1f}s"))
            return build_result(
                success=True,
                platform=downloader.platform_name,
                strategy_used=strategy_name,
                cookie_used=cookie_used,
                deno_used=deno_used,
                output_text=command_result["output_text"],
                raw_stdout=command_result["raw_stdout"],
                raw_stderr=command_result["raw_stderr"],
                selected_format=selected_format,
                output_files=output_files,
                normalized_url=url,
            )

        if command_result.get("cancelled"):
            log_callback(format_strategy_log(downloader.platform_name, strategy_name, "下载已取消"))
            return build_result(
                success=False,
                platform=downloader.platform_name,
                strategy_used=strategy_name,
                cookie_used=cookie_used,
                deno_used=deno_used,
                error_source="STOPPED",
                message="下载已停止。",
                output_text=command_result["output_text"],
                raw_stdout=command_result["raw_stdout"],
                raw_stderr=command_result["raw_stderr"],
                selected_format=selected_format,
                output_files=[],
                normalized_url=url,
            )

        error_source = downloader.classify_error(command_result["raw_stderr"])
        if command_result["returncode"] == 0 and error_source == "UNKNOWN":
            error_source = "OUTPUT_NOT_FOUND"
        error_summary = summarize_command_error(command_result["raw_stderr"], command_result["raw_stdout"])
        if error_summary:
            log_callback(format_strategy_log(downloader.platform_name, strategy_name, f"[yt-dlp stderr]\n{error_summary}"))
        log_callback(format_strategy_log(downloader.platform_name, strategy_name, f"策略失败：{error_source}，耗时 {elapsed:.1f}s"))
        return build_result(
            success=False,
            platform=downloader.platform_name,
            strategy_used=strategy_name,
            cookie_used=cookie_used,
            deno_used=deno_used,
            error_source=error_source,
            message=error_source,
            output_text=command_result["output_text"],
            raw_stdout=command_result["raw_stdout"],
            raw_stderr=command_result["raw_stderr"],
            selected_format=selected_format,
            output_files=[],
            normalized_url=url,
        )
    finally:
        runtime.log_callback = previous_log_callback
        runtime._active_strategy_name = previous_strategy_name
        runtime._last_progress_log_percent = previous_progress_percent
        runtime._last_progress_log_time = previous_progress_time


def get_strategy_label(strategy_name: str) -> str:
    return STRATEGY_LABELS.get(strategy_name or "UNKNOWN", strategy_name or "未知下载策略")


def get_error_message(error_source: str, platform_name: str = "") -> str:
    platform_messages = PLATFORM_ERROR_MESSAGES.get(platform_name or "", {})
    if error_source in platform_messages:
        return platform_messages[error_source]
    return DEFAULT_ERROR_MESSAGES.get(error_source or "UNKNOWN", DEFAULT_ERROR_MESSAGES["UNKNOWN"])


def get_success_message(
    platform_name: str,
    *,
    cookie_used: bool = False,
    deno_used: bool = False,
    selected_format: dict | None = None,
) -> str:
    parts = ["下载成功", platform_name or "未知平台"]
    quality_label = get_quality_label(selected_format)
    if quality_label:
        parts.append(f"画质：{quality_label}")
    if cookie_used:
        parts.append("已走 Cookie")
    else:
        parts.append("未走 Cookie")
    if deno_used:
        parts.append("已使用 Deno 兜底")
    return " | ".join(parts)


def _terminate_process_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except (OSError, ProcessLookupError):
            proc.terminate()


def run_command(cmd: list[str], progress_parser=None, idle_callback=None, cancel_checker=None) -> dict:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    popen_kwargs = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=creationflags,
        encoding="utf-8",
        errors="ignore",
        **popen_kwargs,
    ) as proc:
        stdout_lines = []
        stderr_lines = []
        last_output_time = time.time()

        def mark_output():
            nonlocal last_output_time
            last_output_time = time.time()

        def read_stream(stream, lines, parse_progress=False):
            for line in iter(stream.readline, ""):
                line = line.strip()
                if line:
                    mark_output()
                    lines.append(line)
                    if parse_progress and progress_parser:
                        progress_parser(line)

        stdout_thread = threading.Thread(
            target=read_stream,
            args=(proc.stdout, stdout_lines, True),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(proc.stderr, stderr_lines, True),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        cancelled = False
        while proc.poll() is None:
            if cancel_checker and cancel_checker():
                cancelled = True
                _terminate_process_tree(proc)
                break
            if idle_callback and time.time() - last_output_time >= 6:
                idle_callback()
                last_output_time = time.time()
            time.sleep(0.2)

        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

    stdout_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)
    combined_output = stdout_text + ("\n" + stderr_text if stderr_text else "")
    return {
        "returncode": proc.returncode,
        "raw_stdout": stdout_text,
        "raw_stderr": stderr_text,
        "output_text": combined_output,
        "selected_format": extract_selected_format_metadata(combined_output),
        "output_files": extract_output_file_paths(combined_output),
        "output_ids": extract_output_ids(combined_output),
        "cancelled": cancelled,
    }
