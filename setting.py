# -*- coding: utf-8 -*-
"""Download settings page, cookie health checks, and Deno fallback resolver."""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui_components import UIComponents


if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

DEFAULT_COOKIE_FILE = os.path.join(BASE_DIR, "cookies.txt")
INSTAGRAM_COOKIE_FILE = os.path.join(BASE_DIR, "instagram_cookies.txt")
TIKTOK_COOKIE_FILE = os.path.join(BASE_DIR, "tiktok_cookies.txt")
TWITTER_COOKIE_FILE = os.path.join(BASE_DIR, "twitter_cookies.txt")
COOKIE_EXPIRY_WARNING_DAYS = 7
COOKIE_FAILURE_WARNING_THRESHOLD = 3


class CookieHealthManager:
    """Inspect cookie files and track runtime auth health."""

    PLATFORM_NAME_MAP = {
        "youtube": "YouTube",
        "instagram": "Instagram",
        "tiktok": "TikTok",
        "twitter": "Twitter",
    }
    AUTH_FAILURE_SOURCES = {
        "AUTH_COOKIE_INVALID",
        "AUTH_COOKIE_REQUIRED",
        "AUTH_NEED_LOGIN",
        "AUTH_CAPTCHA",
        "CONTENT_AGE_RESTRICTED",
        "NETWORK_FORBIDDEN",
    }

    @classmethod
    def platform_key(cls, platform_name):
        lowered = (platform_name or "").strip().lower()
        if lowered in cls.PLATFORM_NAME_MAP:
            return lowered
        if lowered in ("youtube", "youtu.be"):
            return "youtube"
        if lowered == "instagram":
            return "instagram"
        if lowered == "tiktok":
            return "tiktok"
        if lowered in ("twitter", "x"):
            return "twitter"
        return lowered or "unknown"

    @classmethod
    def display_name(cls, platform_key):
        return cls.PLATFORM_NAME_MAP.get(platform_key, str(platform_key).title())

    @classmethod
    def empty_status(cls, platform_key, file_path=""):
        return {
            "platform": platform_key,
            "display_name": cls.display_name(platform_key),
            "file": file_path,
            "exists": False,
            "size_bytes": 0,
            "total_entries": 0,
            "valid_entries": 0,
            "session_entries": 0,
            "expired": False,
            "expires_at": None,
            "days_left": None,
            "health_score": 0,
            "health_label": "missing",
            "needs_reimport": False,
            "warning": False,
            "reminder": "Cookie 文件缺失，请重新导入。",
            "consecutive_failures": 0,
            "suspected_invalid": False,
            "last_failure_reason": "",
            "last_failure_at": None,
            "last_success_at": None,
            "last_checked_at": None,
        }

    @classmethod
    def inspect_cookie_file(cls, platform_key, file_path, previous=None):
        status = cls.empty_status(platform_key, file_path)
        previous = previous or {}
        status["consecutive_failures"] = int(previous.get("consecutive_failures", 0) or 0)
        status["suspected_invalid"] = bool(previous.get("suspected_invalid", False))
        status["last_failure_reason"] = previous.get("last_failure_reason", "")
        status["last_failure_at"] = previous.get("last_failure_at")
        status["last_success_at"] = previous.get("last_success_at")
        status["last_checked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if not file_path or not os.path.exists(file_path):
            status["needs_reimport"] = True
            status["warning"] = True
            cls._apply_runtime_health(status)
            return status

        status["exists"] = True
        try:
            status["size_bytes"] = os.path.getsize(file_path)
        except OSError:
            status["size_bytes"] = 0

        now_ts = int(datetime.now(timezone.utc).timestamp())
        total_entries = 0
        valid_entries = 0
        session_entries = 0
        latest_expiry = None

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    if line.startswith("#HttpOnly_"):
                        line = line[len("#HttpOnly_"):]
                    elif line.startswith("#"):
                        continue

                    parts = line.split("\t")
                    if len(parts) < 7:
                        parts = line.split(None, 6)
                    if len(parts) < 7:
                        continue

                    total_entries += 1
                    expiry_raw = parts[4].strip()
                    if expiry_raw.isdigit():
                        expiry_ts = int(expiry_raw)
                        if expiry_ts > now_ts:
                            valid_entries += 1
                            latest_expiry = max(latest_expiry or expiry_ts, expiry_ts)
                    else:
                        session_entries += 1
        except OSError:
            status["exists"] = False
            status["needs_reimport"] = True
            status["warning"] = True
            status["reminder"] = "Cookie 文件无法读取，请重新导入。"
            cls._apply_runtime_health(status)
            return status

        status["total_entries"] = total_entries
        status["valid_entries"] = valid_entries
        status["session_entries"] = session_entries

        if latest_expiry:
            expires_at = datetime.fromtimestamp(latest_expiry, tz=timezone.utc).astimezone()
            status["expires_at"] = expires_at.strftime("%Y-%m-%d %H:%M")
            status["days_left"] = max(0, int((latest_expiry - now_ts) // 86400))

        if total_entries == 0:
            status["health_score"] = 10
            status["needs_reimport"] = True
            status["warning"] = True
            status["reminder"] = "Cookie 文件为空或格式无效，请重新导入。"
        elif valid_entries > 0:
            if status["days_left"] is not None and status["days_left"] <= COOKIE_EXPIRY_WARNING_DAYS:
                status["health_score"] = 72
                status["warning"] = True
                status["reminder"] = f"Cookie 大约还有 {status['days_left']} 天过期，建议尽快重新导出。"
            else:
                status["health_score"] = 95
                status["reminder"] = (
                    f"Cookie valid until {status['expires_at']}"
                    if status["expires_at"]
                    else "Cookie 状态健康。"
                )
        elif session_entries > 0:
            status["health_score"] = 60
            status["warning"] = True
            status["reminder"] = "检测到会话型 Cookie，建议定期重新导出。"
        else:
            status["expired"] = True
            status["health_score"] = 20
            status["needs_reimport"] = True
            status["warning"] = True
            status["reminder"] = "Cookie 看起来已过期，请重新导入。"

        cls._apply_runtime_health(status)
        return status

    @classmethod
    def refresh_status_map(cls, paths_by_platform, previous=None):
        previous = previous or {}
        return {
            platform_key: cls.inspect_cookie_file(platform_key, file_path, previous.get(platform_key, {}))
            for platform_key, file_path in paths_by_platform.items()
        }

    @classmethod
    def _apply_runtime_health(cls, status):
        failures = int(status.get("consecutive_failures", 0) or 0)
        if failures > 0:
            status["health_score"] = max(0, int(status.get("health_score", 0)) - min(45, failures * 15))
            if failures >= COOKIE_FAILURE_WARNING_THRESHOLD:
                status["suspected_invalid"] = True
                status["warning"] = True
                status["needs_reimport"] = True
                reason = status.get("last_failure_reason") or "authentication errors"
                status["reminder"] = f"Cookie 连续失败 {failures} 次（{reason}），疑似失效，请重新导入。"

        score = int(status.get("health_score", 0))
        if status.get("needs_reimport"):
            status["health_label"] = "critical"
        elif score >= 85:
            status["health_label"] = "healthy"
        elif score >= 50:
            status["health_label"] = "warning"
            status["warning"] = True
        else:
            status["health_label"] = "critical"
            status["warning"] = True

    @classmethod
    def record_failure(cls, status_map, platform_name, error_source):
        if error_source not in cls.AUTH_FAILURE_SOURCES:
            return
        platform_key = cls.platform_key(platform_name)
        if platform_key not in status_map:
            status_map[platform_key] = cls.empty_status(platform_key, "")
        status = status_map[platform_key]
        status["consecutive_failures"] = int(status.get("consecutive_failures", 0) or 0) + 1
        status["last_failure_reason"] = error_source
        status["last_failure_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if error_source == "AUTH_COOKIE_INVALID":
            status["suspected_invalid"] = True
            status["needs_reimport"] = True
            status["warning"] = True
            status["reminder"] = "Cookie 已被平台判定为失效，请重新导出并导入。"
        cls._apply_runtime_health(status)

    @classmethod
    def record_success(cls, status_map, platform_name, used_cookie=False):
        if not used_cookie:
            return
        platform_key = cls.platform_key(platform_name)
        if platform_key not in status_map:
            status_map[platform_key] = cls.empty_status(platform_key, "")
        status = status_map[platform_key]
        status["consecutive_failures"] = 0
        status["suspected_invalid"] = False
        status["last_failure_reason"] = ""
        status["last_success_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if status.get("exists") and not status.get("expired") and status.get("valid_entries", 0) > 0:
            status["health_score"] = max(85, int(status.get("health_score", 0) or 0))
            status["needs_reimport"] = False
            status["warning"] = bool(
                status.get("days_left") is not None
                and status.get("days_left") <= COOKIE_EXPIRY_WARNING_DAYS
            )
            status["reminder"] = (
                f"Cookie valid until {status['expires_at']}"
                if status.get("expires_at")
                else "Cookie 登录状态正常。"
            )
        cls._apply_runtime_health(status)

    @classmethod
    def summarize_status(cls, status_map):
        items = []
        all_ready = True
        any_warning = False
        any_reimport = False
        for platform_key in ("youtube", "instagram", "tiktok", "twitter"):
            status = status_map.get(platform_key, cls.empty_status(platform_key, ""))
            display_name = status.get("display_name", cls.display_name(platform_key))
            if not status.get("exists"):
                items.append(f"{display_name}: {'缺失' if str(status.get('file', '') or '').strip() else '未配置'}")
                all_ready = False
                any_warning = True
                any_reimport = True
                continue
            if status.get("needs_reimport"):
                items.append(f"{display_name}: 需重导入")
                all_ready = False
                any_warning = True
                any_reimport = True
                continue
            if status.get("expired"):
                items.append(f"{display_name}: 已过期")
                all_ready = False
                any_warning = True
                any_reimport = True
                continue
            if status.get("days_left") is not None and status.get("days_left") <= COOKIE_EXPIRY_WARNING_DAYS:
                items.append(f"{display_name}: {status['days_left']}天后过期")
                any_warning = True
                continue
            if status.get("session_entries", 0) > 0 and status.get("valid_entries", 0) == 0:
                items.append(f"{display_name}: 会话型")
                any_warning = True
                continue
            items.append(f"{display_name}: 健康")
        return " | ".join(items), all_ready, any_warning, any_reimport

    @classmethod
    def summarize_status_rich(cls, status_map):
        items = []
        for platform_key in ("youtube", "instagram", "tiktok", "twitter"):
            status = status_map.get(platform_key, cls.empty_status(platform_key, ""))
            display_name = status.get("display_name", cls.display_name(platform_key))
            if not status.get("exists"):
                status_text = "缺失" if str(status.get("file", "") or "").strip() else "未配置"
                color = "#f59e0b"
            elif status.get("needs_reimport"):
                status_text = "需重导入"
                color = "#ef4444"
            elif status.get("expired"):
                status_text = "已过期"
                color = "#ef4444"
            elif status.get("days_left") is not None and status.get("days_left") <= COOKIE_EXPIRY_WARNING_DAYS:
                status_text = f"{status['days_left']}天后过期"
                color = "#f59e0b"
            elif status.get("session_entries", 0) > 0 and status.get("valid_entries", 0) == 0:
                status_text = "会话型"
                color = "#f59e0b"
            else:
                status_text = "健康"
                color = "#10b981"
            items.append(f"<span style=\"color:{color};\">{display_name}: {status_text}</span>")
        return " | ".join(items)


class DenoResolver:
    """Small wrapper for the optional Deno fallback resolver."""

    def __init__(self, deno_path, deno_timeout=12):
        self.deno_path = deno_path
        self.deno_timeout = int(deno_timeout or 12)

    def resolve_url(self, url, log_callback=None):
        if not self.deno_path or not os.path.exists(self.deno_path):
            if log_callback:
                log_callback("Deno 未就绪，跳过兜底解析")
            return None

        script_path = os.path.join(BASE_DIR, "scripts", "decrypt.js")
        if not os.path.exists(script_path):
            if log_callback:
                log_callback("Deno 解析脚本不存在，跳过兜底解析")
            return None

        try:
            result = subprocess.run(
                [self.deno_path, "run", "--allow-all", script_path, url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.deno_timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            output = "\n".join(part for part in (result.stdout, result.stderr) if part)
            resolved_url = self._extract_url(output)
            if resolved_url and resolved_url.strip() != (url or "").strip():
                if log_callback:
                    log_callback("Deno 解析完成")
                return {"success": True, "video_url": resolved_url, "raw_output": output}
            if resolved_url:
                if log_callback:
                    log_callback("Deno 未解析出新的媒体地址，跳过无效兜底")
                return None
            if log_callback:
                log_callback(f"Deno 解析失败: {output.strip() or '无输出'}")
        except Exception as exc:
            if log_callback:
                log_callback(f"Deno 解析异常: {exc}")
        return None

    def _extract_url(self, output):
        for line in reversed((output or "").splitlines()):
            line = line.strip()
            for marker in ("解密后URL:", "resolved_url:", "video_url:", "URL:"):
                if marker in line:
                    value = line.split(marker, 1)[1].strip()
                    if value.startswith(("http://", "https://")):
                        return value
            if line.startswith(("http://", "https://")):
                return line
        return ""


class CookieGuideDialog(QDialog):
    """Cookie guide dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cookie 获取指南")
        self.resize(500, 350)
        layout = QVBoxLayout(self)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        text_edit.setFont(QFont("Microsoft YaHei", 10))
        text_edit.setPlainText(
            """
Cookie 获取指南

1. 安装浏览器扩展：
- Get cookies.txt LOCALLY

2. 登录对应平台账号：
- YouTube
- Instagram
- TikTok
- Twitter/X

3. 打开扩展并导出 cookies 文件：
- YouTube: cookies.txt
- Instagram: instagram_cookies.txt
- TikTok: tiktok_cookies.txt
- Twitter/X: twitter_cookies.txt
            """.strip()
        )
        layout.addWidget(text_edit)
        button_layout = QHBoxLayout()
        btn_open = UIComponents.create_button("打开详细教程", 27, 120)
        btn_open.clicked.connect(lambda: webbrowser.open("https://www.muyanshidai.com/jiaocheng/"))
        button_layout.addWidget(btn_open)
        button_layout.addStretch()
        layout.addLayout(button_layout)


class SettingsPage(QWidget):
    """Download settings page."""

    def __init__(self, parent=None, log_handler=None, config=None):
        super().__init__(parent)
        self.parent = parent
        self.log_handler = log_handler
        self.config = config
        self.init_ui()
        self.update_cache_info()
        self.check_cookie_status(log_results=False)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll_area = QScrollArea()
        scroll_area.setObjectName("settingsScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(scroll_area)

        content = QWidget()
        content.setObjectName("settingsContent")
        scroll_area.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(12)

        self._build_download_group(content_layout)
        self._build_cookie_group(content_layout)
        self._build_deno_group(content_layout)
        self._build_cache_group(content_layout)
        content_layout.addStretch()

        self.setStyleSheet(
            """
            QWidget#settingsContent { background-color: #EEF4F7; color: #0F172A; }
            QFrame#settingGroup {
                background: rgba(255, 255, 255, 0.72);
                border: 1px solid #C9DDE8;
                border-radius: 8px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background: white;
                border: 1px solid #B7D1DF;
                border-radius: 6px;
                padding: 4px 8px;
                min-height: 20px;
            }
            QPushButton {
                background: #F8FBFC;
                border: 1px solid #B7D1DF;
                border-radius: 6px;
                color: #17445C;
            }
            QPushButton:hover { background: #E8F3F7; }
            """
        )

    def _build_download_group(self, content_layout):
        group = QFrame()
        group.setObjectName("settingGroup")
        layout = QVBoxLayout(group)

        path_layout = QHBoxLayout()
        path_layout.addWidget(UIComponents.create_label("默认下载路径:"))
        self.download_path_edit = QLineEdit()
        self.download_path_edit.setText(self._config_get("download_path", ""))
        path_layout.addWidget(self.download_path_edit)
        self.btn_browse_path = UIComponents.create_button("浏览", 27, 60)
        self.btn_browse_path.clicked.connect(self.browse_download_path)
        path_layout.addWidget(self.btn_browse_path)
        layout.addLayout(path_layout)

        thread_layout = QHBoxLayout()
        thread_layout.addWidget(UIComponents.create_label("下载线程数:"))
        self.thread_combo = QComboBox()
        for value in ("1", "2", "3", "4", "5", "6", "8"):
            self.thread_combo.addItem(value)
        self.thread_combo.setCurrentText(str(self._config_get("max_threads", 3)))
        thread_layout.addWidget(self.thread_combo)
        thread_layout.addSpacing(18)
        thread_layout.addWidget(UIComponents.create_label("重试次数:"))
        self.retry_combo = QComboBox()
        for value in ("1", "2", "3", "4", "5"):
            self.retry_combo.addItem(value)
        self.retry_combo.setCurrentText(str(self._config_get("retry_count", 3)))
        thread_layout.addWidget(self.retry_combo)
        thread_layout.addSpacing(18)
        self.btn_save_download_settings = UIComponents.create_button("保存设置", 27, 100)
        self.btn_save_download_settings.clicked.connect(self.save_download_settings)
        thread_layout.addWidget(self.btn_save_download_settings)
        thread_layout.addStretch()
        layout.addLayout(thread_layout)
        content_layout.addWidget(group)

    def _build_cookie_group(self, content_layout):
        group = QFrame()
        group.setObjectName("settingGroup")
        layout = QVBoxLayout(group)
        self.cookie_file_edit = self._add_cookie_row(layout, "YouTube Cookie 文件:", "cookie_file", self.browse_cookie_file)
        self.instagram_cookie_edit = self._add_cookie_row(layout, "Instagram Cookie 文件:", "cookie_instagram", self.browse_instagram_cookie_file)
        self.tiktok_cookie_edit = self._add_cookie_row(layout, "TikTok Cookie 文件:", "cookie_tiktok", self.browse_tiktok_cookie_file)
        self.twitter_cookie_edit = self._add_cookie_row(layout, "Twitter Cookie 文件:", "cookie_twitter", self.browse_twitter_cookie_file)

        button_layout = QHBoxLayout()
        self.btn_check_cookie = UIComponents.create_button("检查 Cookie 状态", 27, 120)
        self.btn_check_cookie.clicked.connect(self.check_cookie_status)
        button_layout.addWidget(self.btn_check_cookie)
        self.btn_guide_cookie = UIComponents.create_button("Cookie 获取指南", 27, 120)
        self.btn_guide_cookie.clicked.connect(self.open_cookie_guide)
        button_layout.addWidget(self.btn_guide_cookie)
        self.btn_open_cookie_dir = UIComponents.create_button("打开 Cookie 目录", 27, 120)
        self.btn_open_cookie_dir.clicked.connect(self.open_cookie_directory)
        button_layout.addWidget(self.btn_open_cookie_dir)
        self.btn_save_cookie_settings = UIComponents.create_button("保存设置", 27, 100)
        self.btn_save_cookie_settings.clicked.connect(self.save_cookie_settings)
        button_layout.addWidget(self.btn_save_cookie_settings)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        self.cookie_status_label = QLabel("Cookie 状态: 未检查")
        self.cookie_status_label.setStyleSheet("font-size: 11px; margin-top: 2px; color: #9ca3af;")
        layout.addWidget(self.cookie_status_label)
        content_layout.addWidget(group)

    def _add_cookie_row(self, layout, label_text, config_key, browse_callback):
        row = QHBoxLayout()
        row.addWidget(UIComponents.create_label(label_text))
        edit = QLineEdit()
        edit.setText(self._resolved_path(config_key, ""))
        row.addWidget(edit)
        button = UIComponents.create_button("浏览", 27, 60)
        button.clicked.connect(browse_callback)
        row.addWidget(button)
        layout.addLayout(row)
        return edit

    def _build_deno_group(self, content_layout):
        group = QFrame()
        group.setObjectName("settingGroup")
        layout = QVBoxLayout(group)
        layout.addWidget(UIComponents.create_label("视频解析运行环境", "font-weight: 700;"))

        status_layout = QHBoxLayout()
        self.lbl_deno_status = UIComponents.create_label("", "font-weight: bold;")
        status_layout.addWidget(self.lbl_deno_status)
        status_layout.addStretch()
        layout.addLayout(status_layout)

        path_layout = QHBoxLayout()
        path_layout.addWidget(UIComponents.create_label("运行文件（deno.exe）路径:"))
        self.deno_path_edit = QLineEdit()
        self.deno_path_edit.setText(self._root_tool_path("deno.exe"))
        path_layout.addWidget(self.deno_path_edit)
        self.btn_browse_deno = UIComponents.create_button("浏览", 27, 60)
        self.btn_browse_deno.clicked.connect(self.browse_deno_path)
        path_layout.addWidget(self.btn_browse_deno)
        layout.addLayout(path_layout)

        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(UIComponents.create_label("解析超时(秒):"))
        self.deno_timeout_spin = QSpinBox()
        self.deno_timeout_spin.setRange(10, 60)
        self.deno_timeout_spin.setValue(int(self._config_get("deno_timeout", 12)))
        timeout_layout.addWidget(self.deno_timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)

        self.deno_test_result = QLabel("")
        self.deno_test_result.setStyleSheet("font-size: 11px; color: #64748b;")
        layout.addWidget(self.deno_test_result)
        self._update_deno_status_label()
        content_layout.addWidget(group)

    def _build_cache_group(self, content_layout):
        group = QFrame()
        group.setObjectName("settingGroup")
        layout = QVBoxLayout(group)
        layout.addWidget(UIComponents.create_label("缓存清理", "font-weight: 700;"))
        # Keep status collection available internally without showing it in settings.
        self.tools_status_label = QLabel(group)
        self.tools_status_label.setVisible(False)
        self.cache_info_label = QLabel(group)
        self.cache_info_label.setVisible(False)
        button_layout = QHBoxLayout()
        self.btn_clear_plugin_cache = UIComponents.create_button("清理插件缓存", 27, 120)
        self.btn_clear_plugin_cache.clicked.connect(self.clear_plugin_cache)
        button_layout.addWidget(self.btn_clear_plugin_cache)
        self.btn_clear_log_cache = UIComponents.create_button("清理日志缓存", 27, 120)
        self.btn_clear_log_cache.clicked.connect(self.clear_log_cache)
        button_layout.addWidget(self.btn_clear_log_cache)
        button_layout.addStretch()
        layout.addLayout(button_layout)
        self.update_tools_status()
        content_layout.addWidget(group)

    def update_tools_status(self):
        tools = [
            ("yt-dlp.exe", self._root_tool_path("yt-dlp.exe")),
            ("ffmpeg.exe", self._root_tool_path("ffmpeg.exe")),
            ("ffprobe.exe", self._root_tool_path("ffprobe.exe")),
            ("deno.exe", self._root_tool_path("deno.exe")),
        ]
        parts = [f"{name}: {'已就绪' if os.path.exists(path) else '缺失'}" for name, path in tools]
        self.tools_status_label.setText(" | ".join(parts))
        self._update_deno_status_label()

    def _config_get(self, key, default=None):
        if self.config is None:
            return default
        if hasattr(self.config, "get"):
            return self.config.get(key, default)
        return self.config[key] if key in self.config else default

    def _config_set(self, key, value):
        if self.config is None:
            return
        if hasattr(self.config, "set"):
            self.config.set(key, value)
        else:
            self.config[key] = value

    def _save_plain_config(self):
        if self.config is None or hasattr(self.config, "save_config"):
            return
        with open(os.path.join(BASE_DIR, "config.json"), "w", encoding="utf-8") as file:
            json.dump(self.config, file, indent=2, ensure_ascii=False)

    def _resolved_path(self, key, default=""):
        return (self._config_get(key, default) or "").strip()

    def _root_tool_path(self, file_name):
        return os.path.join(BASE_DIR, file_name)

    def _sync_root_tool_paths(self):
        if self.parent is None:
            return
        tool_map = {
            "yt_dlp_path": "yt-dlp.exe",
            "ffmpeg_path": "ffmpeg.exe",
            "ffprobe_path": "ffprobe.exe",
            "deno_path": "deno.exe",
        }
        for attr, file_name in tool_map.items():
            if hasattr(self.parent, attr):
                setattr(self.parent, attr, self._root_tool_path(file_name))
        if hasattr(self.parent, "enable_deno"):
            self.parent.enable_deno = os.path.exists(self._root_tool_path("deno.exe"))

    def _apply_runtime_paths_from_inputs(self):
        if self.parent is None:
            return
        values = self._cookie_input_values()
        attr_map = {
            "cookie_file": "cookie_file",
            "cookie_instagram": "instagram_cookie_file",
            "cookie_tiktok": "tiktok_cookie_file",
            "cookie_twitter": "twitter_cookie_file",
        }
        for key, attr in attr_map.items():
            if hasattr(self.parent, attr):
                setattr(self.parent, attr, values[key])
        if hasattr(self.parent, "deno_path"):
            self.parent.deno_path = self._root_tool_path("deno.exe")
        if hasattr(self.parent, "deno_timeout"):
            self.parent.deno_timeout = int(self.deno_timeout_spin.value())
        if hasattr(self.parent, "enable_deno"):
            self.parent.enable_deno = os.path.exists(self._root_tool_path("deno.exe"))
        batch_page = getattr(self.parent, "page_batch_extract", None)
        if batch_page is not None and hasattr(batch_page, "set_cookie_files"):
            batch_page.set_cookie_files(
                values["cookie_file"],
                values["cookie_instagram"],
                values["cookie_tiktok"],
                values["cookie_twitter"],
            )
        if hasattr(self.parent, "refresh_cookie_health"):
            self.parent.refresh_cookie_health(log_results=False)
        elif hasattr(self.parent, "update_cookie_status_display"):
            self.parent.update_cookie_status_display()

    def _cookie_input_values(self):
        return {
            "cookie_file": self.cookie_file_edit.text().strip(),
            "cookie_instagram": self.instagram_cookie_edit.text().strip(),
            "cookie_tiktok": self.tiktok_cookie_edit.text().strip(),
            "cookie_twitter": self.twitter_cookie_edit.text().strip(),
        }

    def _log(self, message):
        if self.log_handler:
            self.log_handler.log(message)

    def set_log_handler(self, log_handler):
        self.log_handler = log_handler

    def _persist_cookie_file(self, source_path, target_path, platform_name):
        source_path = (source_path or "").strip()
        if not source_path:
            return ""
        if not os.path.isfile(source_path):
            QMessageBox.warning(self, "错误", f"{platform_name} Cookie 文件不存在，无法保存。")
            return ""
        target_path = os.path.abspath(target_path)
        try:
            if os.path.abspath(source_path) != target_path:
                shutil.copy2(source_path, target_path)
            return target_path
        except OSError as exc:
            QMessageBox.warning(self, "错误", f"{platform_name} Cookie 文件保存失败：\n{exc}")
            return ""

    def _persist_cookie_inputs_to_root(self):
        values = {
            "cookie_file": self._persist_cookie_file(self.cookie_file_edit.text(), DEFAULT_COOKIE_FILE, "YouTube"),
            "cookie_instagram": self._persist_cookie_file(self.instagram_cookie_edit.text(), INSTAGRAM_COOKIE_FILE, "Instagram"),
            "cookie_tiktok": self._persist_cookie_file(self.tiktok_cookie_edit.text(), TIKTOK_COOKIE_FILE, "TikTok"),
            "cookie_twitter": self._persist_cookie_file(self.twitter_cookie_edit.text(), TWITTER_COOKIE_FILE, "Twitter"),
        }
        self.cookie_file_edit.setText(values["cookie_file"])
        self.instagram_cookie_edit.setText(values["cookie_instagram"])
        self.tiktok_cookie_edit.setText(values["cookie_tiktok"])
        self.twitter_cookie_edit.setText(values["cookie_twitter"])
        return values

    def browse_download_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择默认下载路径")
        if path:
            self.download_path_edit.setText(path)

    def browse_cookie_file(self):
        self._browse_cookie(self.cookie_file_edit, DEFAULT_COOKIE_FILE, "YouTube")

    def browse_instagram_cookie_file(self):
        self._browse_cookie(self.instagram_cookie_edit, INSTAGRAM_COOKIE_FILE, "Instagram")

    def browse_tiktok_cookie_file(self):
        self._browse_cookie(self.tiktok_cookie_edit, TIKTOK_COOKIE_FILE, "TikTok")

    def browse_twitter_cookie_file(self):
        self._browse_cookie(self.twitter_cookie_edit, TWITTER_COOKIE_FILE, "Twitter")

    def _browse_cookie(self, edit, target_path, platform_name):
        file_path, _ = QFileDialog.getOpenFileName(self, f"选择 {platform_name} Cookie 文件", "", "Cookie 文件 (*.txt)")
        if file_path:
            edit.setText(self._persist_cookie_file(file_path, target_path, platform_name))
            self._apply_runtime_paths_from_inputs()
            self.check_cookie_status(log_results=True)

    def browse_deno_path(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "选择 Deno.exe 文件", BASE_DIR, "可执行文件 (*.exe);;所有文件 (*.*)")
        if file_path:
            self.deno_path_edit.setText(file_path)
            self._apply_runtime_paths_from_inputs()
            self._update_deno_status_label()

    def check_cookie_status(self, checked=False, log_results=True):
        status_map = CookieHealthManager.refresh_status_map(self._cookie_input_values())
        status_text, all_ready, any_warning, any_reimport = CookieHealthManager.summarize_status(status_map)
        status_text_rich = CookieHealthManager.summarize_status_rich(status_map)
        if all_ready:
            self.cookie_status_label.setText(f"所有 Cookie 健康 | {status_text_rich}")
            self.cookie_status_label.setStyleSheet("font-size: 11px; margin-top: 2px; color: #10b981;")
        elif any_reimport:
            self.cookie_status_label.setText(f"按平台检查 Cookie | {status_text_rich}")
            self.cookie_status_label.setStyleSheet("font-size: 11px; margin-top: 2px; color: #f59e0b;")
        elif any_warning:
            self.cookie_status_label.setText(f"Cookie 需要关注 | {status_text_rich}")
            self.cookie_status_label.setStyleSheet("font-size: 11px; margin-top: 2px; color: #f59e0b;")
        else:
            self.cookie_status_label.setText(f"按平台检查 Cookie | {status_text_rich}")
            self.cookie_status_label.setStyleSheet("font-size: 11px; margin-top: 2px; color: #f59e0b;")
        if log_results:
            for platform_key in ("youtube", "instagram", "tiktok", "twitter"):
                status = status_map[platform_key]
                self._log(
                    f"Cookie health | {status['display_name']}: {status['health_label']} | "
                    f"score={status['health_score']} | {status['reminder']}"
                )
        return status_map

    def open_cookie_guide(self):
        CookieGuideDialog(self).exec()

    def open_cookie_directory(self):
        if not os.path.exists(BASE_DIR):
            QMessageBox.warning(self, "错误", "无法打开 Cookie 目录")
            return
        try:
            if platform.system().lower().startswith("win") and hasattr(os, "startfile"):
                os.startfile(BASE_DIR)
            else:
                webbrowser.open(BASE_DIR)
        except Exception as exc:
            QMessageBox.warning(self, "错误", f"无法打开 Cookie 目录:\n{exc}")

    def _update_deno_status_label(self):
        enabled = os.path.exists(self.deno_path_edit.text().strip())
        if enabled:
            self.lbl_deno_status.setText("视频解析运行环境已就绪，将自动参与平台解析")
            self.lbl_deno_status.setStyleSheet("color: #10b981; font-weight: bold;")
        else:
            self.lbl_deno_status.setText("视频解析运行环境未就绪")
            self.lbl_deno_status.setStyleSheet("color: #ef4444; font-weight: bold;")

    def test_deno_function(self):
        deno_path = self.deno_path_edit.text().strip()
        if not os.path.exists(deno_path):
            self.deno_test_result.setText("Deno.exe 文件不存在")
            QMessageBox.warning(self, "测试失败", f"Deno.exe 文件不存在:\n{deno_path}")
            return
        self.deno_test_result.setText("Deno.exe 已存在")

    def clear_plugin_cache(self):
        removed = self._remove_files_by_suffix(BASE_DIR, (".tmp", ".part"))
        self.update_cache_info()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 个插件缓存文件")

    def clear_log_cache(self):
        logs_dir = os.path.join(BASE_DIR, "logs")
        removed = self._remove_files_by_suffix(logs_dir, (".log",)) if os.path.isdir(logs_dir) else 0
        self.update_cache_info()
        QMessageBox.information(self, "清理完成", f"已清理 {removed} 个日志文件")

    def _remove_files_by_suffix(self, folder, suffixes):
        removed = 0
        if not os.path.isdir(folder):
            return removed
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name.lower().endswith(suffixes):
                try:
                    os.remove(path)
                    removed += 1
                except OSError as exc:
                    self._log(f"删除文件失败 {path}: {exc}")
        return removed

    def update_cache_info(self):
        def folder_stats(folder):
            total = 0
            count = 0
            if os.path.isdir(folder):
                for root, _, files in os.walk(folder):
                    for name in files:
                        path = os.path.join(root, name)
                        try:
                            total += os.path.getsize(path)
                            count += 1
                        except OSError:
                            pass
            return count, total

        logs_count, logs_size = folder_stats(os.path.join(BASE_DIR, "logs"))
        cache_count, cache_size = folder_stats(os.path.join(BASE_DIR, "cache"))
        self.cache_info_label.setText(
            f"缓存: {cache_count} 个文件，{self._format_file_size(cache_size)} | "
            f"日志: {logs_count} 个文件，{self._format_file_size(logs_size)}"
        )

    def _format_file_size(self, size_bytes):
        size = float(size_bytes or 0)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def _save_settings(self, save_download=False, save_cookie=False):
        updates = {}
        if save_cookie:
            updates.update(self._persist_cookie_inputs_to_root())
        if save_download:
            updates.update(
                {
                    "download_path": self.download_path_edit.text().strip(),
                    "max_threads": int(self.thread_combo.currentText()),
                    "retry_count": int(self.retry_combo.currentText()),
                    "deno_timeout": int(self.deno_timeout_spin.value()),
                }
            )
        for key, value in updates.items():
            self._config_set(key, value)
        self._save_plain_config()
        self._sync_root_tool_paths()
        self._apply_runtime_paths_from_inputs()
        self.update_tools_status()
        self.check_cookie_status(log_results=False)
        self._log("设置已保存")
        QMessageBox.information(self, "成功", "设置已保存。")

    def save_download_settings(self):
        self._save_settings(save_download=True, save_cookie=False)

    def save_cookie_settings(self):
        self._save_settings(save_download=False, save_cookie=True)

    def save_settings(self):
        self._save_settings(save_download=True, save_cookie=True)

    def set_config(self, config):
        self.config = config
        self.download_path_edit.setText(self._config_get("download_path", ""))
        self.thread_combo.setCurrentText(str(self._config_get("max_threads", 3)))
        self.retry_combo.setCurrentText(str(self._config_get("retry_count", 3)))
        self.cookie_file_edit.setText(self._resolved_path("cookie_file", ""))
        self.instagram_cookie_edit.setText(self._resolved_path("cookie_instagram", ""))
        self.tiktok_cookie_edit.setText(self._resolved_path("cookie_tiktok", ""))
        self.twitter_cookie_edit.setText(self._resolved_path("cookie_twitter", ""))
        self.deno_timeout_spin.setValue(int(self._config_get("deno_timeout", 12)))
        self.deno_path_edit.setText(self._root_tool_path("deno.exe"))
        self._sync_root_tool_paths()
        self._update_deno_status_label()
        self.check_cookie_status(log_results=False)
