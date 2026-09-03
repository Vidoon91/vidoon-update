import os
import random
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from core.download_types import DownloadRuntime
from core.youtube_pot_provider import get_youtube_pot_provider
from core.download_utils import (
    DOWNLOAD_PROGRESS_TEMPLATE,
    build_output_file_print_template,
    build_output_id_print_template,
    build_selected_format_print_template,
    maybe_log_download_progress,
    parse_download_progress,
    run_logged_download_strategy,
)


YOUTUBE_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
]

YOUTUBE_QUALITY_SELECTOR = (
    "bestvideo[height>=2160][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height>=1440][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/"
    "best[height>=720]"
)

YOUTUBE_SHORTS_QUALITY_SELECTOR = (
    "bestvideo[width>=2160][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[width>=1440][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[width>=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[width>=720][ext=mp4]+bestaudio[ext=m4a]/"
    "best[width>=720]"
)

YOUTUBE_SORT_SELECTOR = "res,fps,br"


def _version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _detect_chromium_version(exe_path: str) -> str:
    if not exe_path or not os.path.exists(exe_path):
        return ""

    app_dir = os.path.dirname(exe_path)
    try:
        versions = [
            name for name in os.listdir(app_dir)
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", name)
        ]
    except Exception:
        versions = []

    if versions:
        return sorted(versions, key=_version_key, reverse=True)[0]
    return ""


def get_browser_user_agent() -> str:
    if os.name != "nt":
        return ""

    candidates = [
        (
            "chrome",
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ),
        (
            "chrome",
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ),
        (
            "chrome",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        ),
        (
            "edge",
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        ),
        (
            "edge",
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        ),
    ]

    for browser_name, exe_path in candidates:
        version = _detect_chromium_version(exe_path)
        if not version:
            continue
        base = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{version} Safari/537.36"
        )
        if browser_name == "edge":
            return f"{base} Edg/{version}"
        return base

    return ""


class YouTubeDownloader:
    platform_name = "YouTube"

    def __init__(self, runtime: DownloadRuntime):
        self.runtime = runtime
        if runtime.youtube_pot_provider is None:
            app_dir = os.path.dirname(os.path.abspath(runtime.yt_dlp_path))
            runtime.youtube_pot_provider = get_youtube_pot_provider(app_dir)

    def normalize_url(self, url: str) -> dict:
        original_url = url.strip()
        parsed = urlparse(original_url)
        scheme = parsed.scheme or "https"
        netloc = parsed.netloc or "www.youtube.com"
        query = parse_qs(parsed.query)
        path = parsed.path
        is_shorts = "/shorts/" in path

        if "youtu.be" in netloc:
            video_id = parsed.path.strip("/")
            path = "/watch"
            query = {"v": [video_id]}
            netloc = "www.youtube.com"

        kept = {}
        # This application downloads individual videos, so playlist/radio
        # parameters only add unnecessary metadata requests.
        for key in ("v", "t", "time_continue"):
            if key in query:
                kept[key] = query[key][0]

        normalized_url = urlunparse((scheme, netloc, path, "", urlencode(kept), parsed.fragment or ""))
        return {
            "success": True,
            "original_url": original_url,
            "normalized_url": normalized_url,
            "platform": self.platform_name,
            "url_modified": normalized_url != original_url,
            "modification_reason": "normalized YouTube URL" if normalized_url != original_url else "no changes",
            "is_shorts": is_shorts,
            "message": "YouTube URL normalized (Shorts)" if is_shorts else "YouTube URL normalized",
        }

    def classify_error(self, stderr_text: str) -> str:
        error_text = (stderr_text or "").lower()
        patterns = [
            (["http error 429", "too many requests", "rate limit"], "NETWORK_RATE_LIMIT"),
            (["http error 403", "403 forbidden"], "YOUTUBE_HTTP_403"),
            ([
                "sign in to confirm you're not a bot",
                "confirm youre not a bot",
                "not a bot",
                "bot check",
                "missing a valid po_token",
                "po_token",
            ], "AUTH_BOT_CHECK"),
            (["cookies are no longer valid", "provided youtube account cookies are no longer valid"], "AUTH_COOKIE_INVALID"),
            (["http error 416", "requested range not satisfiable"], "DOWNLOAD_RANGE_INVALID"),
            (["confirm your age", "age-restricted", "age restricted"], "CONTENT_AGE_RESTRICTED"),
            (["private video", "this video is private"], "PRIVATE_VIDEO"),
            ([
                "login required",
                "sign in to watch",
                "sign in to view",
                "members-only content",
                "members only",
                "authentication required",
            ], "AUTH_NEED_LOGIN"),
            (["unable to extract", "failed to parse", "signature", "n-sig", "no video formats", "unsupported url"], "EXTRACTOR_FAILED"),
            ([
                "timed out",
                "timeout",
                "connection reset",
                "connection refused",
                "failed to establish a new connection",
                "network is unreachable",
                "tls connect error",
                "sslerror",
            ], "NETWORK_ERROR"),
        ]
        for keys, value in patterns:
            if any(key in error_text for key in keys):
                return value
        return "UNKNOWN"

    def should_use_cookie(self, error_source: str) -> bool:
        return error_source in {"AUTH_NEED_LOGIN", "CONTENT_AGE_RESTRICTED", "PRIVATE_VIDEO", "AUTH_BOT_CHECK"}

    def should_use_advanced_auth(self, error_source: str) -> bool:
        return error_source in {"AUTH_BOT_CHECK", "AUTH_NEED_LOGIN", "EXTRACTOR_FAILED"}

    def should_use_format_fallback(self, error_source: str) -> bool:
        return error_source == "YOUTUBE_HTTP_403"

    def is_ip_risk_error(self, error_source: str) -> bool:
        return error_source in {
            "NETWORK_RATE_LIMIT",
            "AUTH_BOT_CHECK",
        }

    def _friendly_message(self, error_source: str) -> str:
        messages = {
            "NETWORK_RATE_LIMIT": "当前 IP 请求过多，已被 YouTube 限流，下载已停止。请更换 VPN 节点或稍后再试。",
            "AUTH_COOKIE_INVALID": "这份 YouTube Cookie 已失效，请重新导出并导入。",
            "AUTH_BOT_CHECK": "当前 IP/VPN 节点已触发 YouTube 风控或机器人验证，下载已停止。请更换 VPN 节点、切换出口 IP，或降低下载频率后重试。",
            "NETWORK_ERROR": "网络连接异常，请检查网络或 VPN 后重试。",
            "CONTENT_AGE_RESTRICTED": "该视频有年龄限制，请使用有效的 YouTube Cookie。",
            "PRIVATE_VIDEO": "该视频是私密视频，当前账号无权访问。",
            "AUTH_NEED_LOGIN": "这个 YouTube 视频需要登录后才能下载，请导入可用的 YouTube Cookie。",
            "YOUTUBE_HTTP_403": "YouTube 拒绝了当前媒体下载通道（HTTP 403），工具已自动尝试其他可用通道。",
            "EXTRACTOR_FAILED": "YouTube 解析失败，请更新 yt-dlp 或稍后重试。",
        }
        return messages.get(error_source, "下载失败，请检查链接、网络或更新 yt-dlp。")

    def execute_download(self, url_info: dict, log_callback) -> dict:
        normalized_url = url_info["normalized_url"]
        is_shorts = bool(url_info.get("is_shorts", False))

        log_callback("📋 平台: YouTube")
        if is_shorts:
            log_callback("📱 检测到 Shorts 视频")

        result = self._download_platform_without_cookie(normalized_url, log_callback, is_shorts)
        if result["success"]:
            return result

        error_source = result["error_source"]
        if error_source == "NETWORK_RATE_LIMIT":
            log_callback("⛔ YouTube 下载已停止：当前 IP/VPN 节点触发平台风控")
            log_callback("原因：该 IP 可能请求过多、被 YouTube 标记为异常环境，继续重试无意义")
            log_callback("处理建议：请更换 VPN 节点 / 更换出口 IP / 降低下载频率后重新下载")
            result["message"] = self._friendly_message(error_source)
            return result

        if error_source in {"AUTH_COOKIE_INVALID", "NETWORK_ERROR"}:
            result["message"] = self._friendly_message(error_source)
            return result

        if self.should_use_cookie(error_source):
            if self._has_cookie_file():
                result = self._download_platform_with_cookie(normalized_url, log_callback, is_shorts)
                if result["success"]:
                    return result
                if self.is_ip_risk_error(result["error_source"]):
                    result["message"] = self._friendly_message(result["error_source"])
                    return result
                if result["error_source"] in {
                    "AUTH_COOKIE_INVALID",
                    "NETWORK_ERROR",
                }:
                    result["message"] = self._friendly_message(result["error_source"])
                    return result
            else:
                log_callback("⚠️ 当前错误需要 YouTube Cookie，但未找到 cookies.txt")
                if error_source == "AUTH_BOT_CHECK":
                    result["message"] = self._friendly_message(error_source)
                    return result

        if self.should_use_advanced_auth(result["error_source"]):
            if self._has_cookie_file() and self._has_advanced_auth_params():
                result = self._download_platform_with_advanced_auth(normalized_url, log_callback, is_shorts)
                if result["success"]:
                    return result
                if self.is_ip_risk_error(result["error_source"]):
                    result["message"] = self._friendly_message(result["error_source"])
                    return result
                if result["error_source"] in {"AUTH_COOKIE_INVALID", "NETWORK_ERROR"}:
                    result["message"] = self._friendly_message(result["error_source"])
                    return result
            elif self._has_cookie_file():
                log_callback("策略3：自动增强机器人验证处理已关闭，跳过高级验证参数模式")

        if self.runtime.youtube_format_fallback and self.should_use_format_fallback(result["error_source"]):
            original_result = result
            quality_axis, _original_quality = self._selected_quality_requirement(
                result.get("selected_format")
            )
            quality_axis = quality_axis or ("width" if is_shorts else "height")

            provider_result = self._download_with_pot_provider(
                normalized_url,
                log_callback,
                is_shorts,
                quality_axis=quality_axis,
            )
            if provider_result and provider_result["success"]:
                return provider_result

            result = self._download_hls_fallback(
                normalized_url,
                log_callback,
                is_shorts,
                quality_axis=quality_axis,
                minimum_quality=720,
            )
            if result["success"]:
                return result
            log_callback("未找到 720P 或以上的可用备用通道，停止下载，不再降低清晰度")
            result = original_result

        result["message"] = self._friendly_message(result.get("error_source", "UNKNOWN"))
        return result

    def _has_cookie_file(self) -> bool:
        return bool(self.runtime.cookie_file and os.path.exists(self.runtime.cookie_file))

    def _has_advanced_auth_params(self) -> bool:
        return bool(self.runtime.youtube_advanced_auth_enabled)

    def _download_platform_without_cookie(self, url: str, log_callback, is_shorts: bool) -> dict:
        log_callback("策略1：YouTube 无 Cookie 下载...")
        cmd = self._build_youtube_command(url, is_shorts, use_cookie=False)
        return self._run_strategy(cmd, url, log_callback, "PlatformNoCookie", cookie_used=False)

    def _download_platform_with_cookie(self, url: str, log_callback, is_shorts: bool) -> dict:
        log_callback("策略2：YouTube Cookie 下载...")
        cmd = self._build_youtube_command(url, is_shorts, use_cookie=True)
        log_callback("🍪 使用 YouTube cookies.txt")
        return self._run_strategy(cmd, url, log_callback, "PlatformCookie", cookie_used=True)

    def _download_platform_with_advanced_auth(self, url: str, log_callback, is_shorts: bool) -> dict:
        log_callback("策略3：YouTube Cookie + 高级验证参数...")
        cmd = self._build_youtube_command(url, is_shorts, use_cookie=True, use_advanced_auth=True)
        return self._run_strategy(cmd, url, log_callback, "YouTubeAdvancedAuth", cookie_used=True)

    @staticmethod
    def _selected_quality_requirement(selected_format: dict | None) -> tuple[str, int]:
        resolution = str((selected_format or {}).get("resolution", "") or "").strip()
        dimensions = re.fullmatch(r"(?P<width>\d{2,5})x(?P<height>\d{2,5})", resolution)
        if dimensions:
            width = int(dimensions.group("width"))
            height = int(dimensions.group("height"))
            return ("height", height) if width >= height else ("width", width)

        progressive = re.search(r"(?<!\d)(?P<height>\d{3,4})p(?!\d)", resolution, re.IGNORECASE)
        if progressive:
            return "height", int(progressive.group("height"))
        return "", 0

    def _download_hls_fallback(
        self,
        url: str,
        log_callback,
        is_shorts: bool,
        *,
        quality_axis: str,
        minimum_quality: int,
    ) -> dict:
        log_callback(
            f"策略5：继续尝试不低于 {minimum_quality}P 的 HLS 备用通道..."
        )
        cmd = self._build_youtube_command(
            url,
            is_shorts,
            use_cookie=False,
            fallback_mode="hls",
            minimum_quality=minimum_quality,
            quality_axis=quality_axis,
        )
        return self._run_strategy(
            cmd,
            url,
            log_callback,
            "YouTubeHlsFallback",
            cookie_used=False,
        )

    def _download_with_pot_provider(
        self,
        url: str,
        log_callback,
        is_shorts: bool,
        *,
        quality_axis: str,
    ) -> dict | None:
        provider = self.runtime.youtube_pot_provider
        if provider is None:
            log_callback("策略4：本地 PO Token Provider 未配置，跳过验证通道")
            return None

        ready, reason = provider.ensure_ready(log_callback)
        if not ready:
            reason_messages = {
                "provider_not_installed": "Provider 运行文件不完整，请重新执行打包准备",
                "provider_start_failed": "Provider 进程启动失败",
                "provider_exited": "Provider 启动后异常退出",
                "provider_timeout": "Provider 启动超时",
            }
            log_callback(f"策略4：{reason_messages.get(reason, 'Provider 当前不可用')}，继续尝试 HLS 通道")
            return None

        log_callback("策略4：启用本地 PO Token Provider，重新请求 720P 或以上媒体通道...")
        cmd = self._build_youtube_command(
            url,
            is_shorts,
            use_cookie=False,
            fallback_mode="pot",
            minimum_quality=720,
            quality_axis=quality_axis,
            provider_base_url=provider.base_url,
        )
        return self._run_strategy(
            cmd,
            url,
            log_callback,
            "YouTubePoToken",
            cookie_used=False,
        )

    def _build_youtube_command(
        self,
        url: str,
        is_shorts: bool,
        *,
        use_cookie: bool,
        use_deno_url: bool = False,
        use_advanced_auth: bool = False,
        fallback_mode: str = "",
        minimum_quality: int = 0,
        quality_axis: str = "height",
        provider_base_url: str = "",
    ) -> list[str]:
        cmd = self._build_base_command()
        if fallback_mode != "pot":
            # The portable BgUtils plugin probes its local server when loaded.
            # Normal downloads do not need it; load it only for the 403 retry.
            cmd.append("--no-plugin-dirs")
        cmd.extend(["--merge-output-format", "mp4", "--remux-video", "mp4"])
        if fallback_mode == "hls":
            axis = quality_axis if quality_axis in {"width", "height"} else "height"
            quality_filter = f"[{axis}>={int(minimum_quality)}]" if minimum_quality else ""
            quality_selector = (
                f"best[protocol^=m3u8]{quality_filter}/"
                f"bestvideo[protocol^=m3u8]{quality_filter}+bestaudio"
            )
        elif fallback_mode == "pot":
            axis = quality_axis if quality_axis in {"width", "height"} else "height"
            quality_filter = f"[{axis}>={max(720, int(minimum_quality or 720))}]"
            quality_selector = (
                f"bestvideo{quality_filter}[ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo{quality_filter}+bestaudio/"
                f"best{quality_filter}"
            )
        else:
            quality_selector = YOUTUBE_SHORTS_QUALITY_SELECTOR if is_shorts else YOUTUBE_QUALITY_SELECTOR

        cmd.extend(
            [
                "--add-header",
                "Referer:https://www.youtube.com/",
                "--postprocessor-args",
                "ffmpeg:-c:v copy -c:a aac -b:a 192k -movflags +faststart",
                "--concurrent-fragments",
                "3",
                "-S",
                YOUTUBE_SORT_SELECTOR,
                "--print",
                build_selected_format_print_template(),
                "--print",
                build_output_file_print_template(),
                "--print",
                build_output_id_print_template(),
                "-f",
                quality_selector,
                "-o",
                os.path.join(self.runtime.save_path, "%(title)s-%(id)s.%(ext)s"),
            ]
        )

        player_client = {
            "hls": "web_safari",
            "pot": "mweb",
        }.get(fallback_mode, "")
        extractor_args = self._build_extractor_args(
            is_shorts,
            use_deno_url,
            use_advanced_auth,
            player_client=player_client,
        )
        if extractor_args:
            cmd.extend(["--extractor-args", extractor_args])

        if fallback_mode == "pot" and provider_base_url:
            plugin_dir = os.path.join(
                os.path.dirname(os.path.abspath(self.runtime.yt_dlp_path)),
                "yt-dlp-plugins",
            )
            cmd.extend(["--plugin-dirs", plugin_dir])
            cmd.extend(
                [
                    "--extractor-args",
                    f"youtubepot-bgutilhttp:base_url={provider_base_url}",
                ]
            )

        if use_cookie and self._has_cookie_file():
            cmd.extend(["--cookies", self.runtime.cookie_file])

        cmd.append(url)
        return cmd

    def _build_extractor_args(
        self,
        is_shorts: bool,
        use_deno_url: bool,
        use_advanced_auth: bool,
        *,
        player_client: str = "",
    ) -> str:
        if use_advanced_auth and (self.runtime.youtube_advanced_extractor_args or "").strip():
            return self.runtime.youtube_advanced_extractor_args.strip()

        segments = []
        if player_client:
            segments.append(f"player_client={player_client}")
        elif use_deno_url or use_advanced_auth:
            default_clients = "web,android" if is_shorts else "web,android,ios"
            segments.append(f"player_client={default_clients}")

        if use_advanced_auth:
            visitor_data = (self.runtime.youtube_visitor_data or "").strip()
            po_token = (self.runtime.youtube_po_token or "").strip()
            po_context = (self.runtime.youtube_po_token_context or "web.gvs").strip() or "web.gvs"
            if visitor_data:
                segments.append(f"visitor_data={visitor_data}")
            if po_token:
                token_value = po_token if "+" in po_token else f"{po_context}+{po_token}"
                segments.append(f"po_token={token_value}")

        return "youtube:" + ";".join(segments) if segments else ""

    def _build_base_command(self) -> list[str]:
        user_agent = self._get_user_agent()
        cmd = [
            self.runtime.yt_dlp_path,
            "--no-playlist",
            "--ignore-errors",
            "--continue",
            "--newline",
            "--progress",
            "--progress-template",
            DOWNLOAD_PROGRESS_TEMPLATE,
            "--ffmpeg-location",
            self.runtime.ffmpeg_path,
            "--user-agent",
            user_agent,
            "--socket-timeout",
            "60",
            "--retries",
            "5",
            "--retry-sleep",
            "http:linear=5:15:2",
            "--retry-sleep",
            "fragment:linear=5:15:2",
            "--sleep-requests",
            "1",
        ]
        if self.runtime.enable_deno and self.runtime.deno_path and os.path.exists(self.runtime.deno_path):
            cmd.extend(["--js-runtimes", f"deno:{self.runtime.deno_path}"])
        if self.runtime.write_thumbnail:
            cmd.append("--write-thumbnail")
        return cmd

    def _get_user_agent(self) -> str:
        configured = (self.runtime.youtube_user_agent or "").strip()
        if configured and configured.startswith("Mozilla/"):
            return configured

        if self.runtime.youtube_use_browser_user_agent:
            browser_user_agent = get_browser_user_agent()
            if browser_user_agent:
                return browser_user_agent

        return random.choice(YOUTUBE_USER_AGENTS)

    def _run_strategy(
        self,
        cmd: list[str],
        url: str,
        log_callback,
        strategy_name: str,
        *,
        cookie_used: bool,
        deno_used: bool = False,
    ) -> dict:
        return run_logged_download_strategy(
            self,
            cmd,
            url,
            log_callback,
            strategy_name,
            cookie_used=cookie_used,
            deno_used=deno_used,
        )

    def _parse_progress(self, line: str):
        title_match = re.search(r"\[download\] Destination:\s+(.+)", line)
        if title_match and self.runtime.progress_callback:
            self.runtime.progress_callback("title", title_match.group(1))

        progress = parse_download_progress(line)
        if not progress:
            return

        previous_percent = getattr(self.runtime, "_current_video_progress_percent", 0.0)
        current_percent = float(progress.get("percent", 0.0) or 0.0)
        if current_percent + 0.05 < previous_percent:
            return
        self.runtime._current_video_progress_percent = max(previous_percent, current_percent)

        if self.runtime.progress_callback:
            self.runtime.progress_callback("progress", progress)
        if self.runtime.speed_callback and progress.get("speed"):
            self.runtime.speed_callback(progress["speed"])
        maybe_log_download_progress(
            self.runtime,
            self.platform_name,
            getattr(self.runtime, "_active_strategy_name", ""),
            progress,
        )
