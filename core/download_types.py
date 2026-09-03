from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class DownloadRuntime:
    yt_dlp_path: str
    ffmpeg_path: str
    save_path: str
    deno_path: str = ""
    deno_timeout: int = 12
    enable_deno: bool = False
    cookie_file: str = ""
    instagram_cookie_file: str = ""
    tiktok_cookie_file: str = ""
    twitter_cookie_file: str = ""
    cookie_status: dict[str, Any] = field(default_factory=dict)
    youtube_visitor_data: str = ""
    youtube_po_token: str = ""
    youtube_po_token_context: str = "web.gvs"
    youtube_advanced_extractor_args: str = ""
    youtube_advanced_auth_enabled: bool = True
    youtube_format_fallback: bool = True
    youtube_user_agent: str = ""
    youtube_use_browser_user_agent: bool = True
    youtube_pot_provider: Any = None
    write_thumbnail: bool = False
    log_callback: Callable[[str], None] | None = None
    speed_callback: Callable[[str], None] | None = None
    progress_callback: Callable[[str, Any], None] | None = None
    cancel_checker: Callable[[], bool] | None = None


def build_result(
    *,
    success: bool,
    platform: str,
    strategy_used: str,
    cookie_used: bool = False,
    error_source: str = "",
    message: str = "",
    deno_used: bool = False,
    output_text: str = "",
    raw_stdout: str = "",
    raw_stderr: str = "",
    selected_format: dict[str, Any] | None = None,
    output_files: list[str] | None = None,
    has_audio: bool = True,
    normalized_url: str = "",
    url_modified: bool = False,
    modification_reason: str = "",
) -> dict[str, Any]:
    return {
        "success": success,
        "platform": platform,
        "strategy_used": strategy_used,
        "cookie_used": cookie_used,
        "error_source": error_source,
        "message": message,
        "deno_used": deno_used,
        "output_text": output_text,
        "raw_stdout": raw_stdout,
        "raw_stderr": raw_stderr,
        "selected_format": selected_format,
        "output_files": list(output_files or []),
        "output_file": output_files[0] if output_files else "",
        "has_audio": has_audio,
        "normalized_url": normalized_url,
        "url_modified": url_modified,
        "modification_reason": modification_reason,
    }
