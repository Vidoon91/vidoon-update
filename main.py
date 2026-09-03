# -*- coding: utf-8 -*-
"""Vidoon video downloader GUI."""

import sys
import os
import platform
import json
import threading
import subprocess
import re
import multiprocessing
import webbrowser
import ctypes
from datetime import datetime
from collections import deque

from app_config import get_app_value
from auto_update import AutoUpdateManager

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QProgressBar, QFileDialog,
    QMessageBox, QComboBox, QFrame, QSizePolicy,
    QStackedWidget, QSpacerItem, QStatusBar,
    QLineEdit, QTabWidget, QDialog,
    QMenu, QToolButton
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QEasingCurve, QRect, QVariantAnimation, QLockFile
from PySide6.QtGui import QTextCursor, QIcon, QFontMetrics, QPainter, QColor, QBrush, QLinearGradient, QPixmap

#
from shouquan import (
    USER_DATA_DIR,
    load_auth_data,
    save_account_session,
    clear_auth_data,
    AuthCacheManager,
    ensure_authorized,
    send_email_verification_code,
    register_account_with_server,
    reset_password_with_email,
    login_account_with_server,
    get_public_site_config,
    create_ad_reward_session,
    get_ad_reward_status,
    reserve_download_permission,
    settle_download_permission,
    logout_account_with_server,
)

#
from rizhi import LogHandler

#
from setting import SettingsPage, CookieHealthManager
from ui_components import UIComponents

# ------------------- Config -------------------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
YT_DLP_NAME = "yt-dlp.exe"
FFMPEG_NAME = "ffmpeg.exe"
FFPROBE_NAME = "ffprobe.exe"
DENO_NAME = "deno.exe"
ICON_FILE = os.path.join(BASE_DIR, "icon.ico")
DEFAULT_COOKIE_FILE = os.path.join(BASE_DIR, "cookies.txt")
INSTAGRAM_COOKIE_FILE = os.path.join(BASE_DIR, "instagram_cookies.txt")
TIKTOK_COOKIE_FILE = os.path.join(BASE_DIR, "tiktok_cookies.txt")
TWITTER_COOKIE_FILE = os.path.join(BASE_DIR, "twitter_cookies.txt")
WINDOW_TITLE = "Vidoon视频素材工具 2026版"
RESTART_ARG = "--vidoon-restart"
PACKAGE_SELF_TEST_ARG = "--package-self-test"
INSTANCE_LOCK_FILE = os.path.join(USER_DATA_DIR, "Vidoon2026.lock")
INSTANCE_LOCK = None


def get_restart_command():
    """Return a clean command for relaunching the current app."""
    if getattr(sys, "frozen", False):
        return [sys.executable, RESTART_ARG]

    script_path = os.path.abspath(sys.argv[0]) if sys.argv else os.path.abspath(__file__)
    args = [arg for arg in sys.argv[1:] if arg != RESTART_ARG]
    return [sys.executable, script_path, RESTART_ARG] + args


def get_restart_creationflags():
    if os.name != "nt":
        return 0

    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    return flags


def acquire_single_instance_lock():
    """Keep only one local app window running per install folder."""
    global INSTANCE_LOCK
    lock = QLockFile(INSTANCE_LOCK_FILE)
    lock.setStaleLockTime(0)
    timeout_ms = 8000 if RESTART_ARG in sys.argv else 0

    if lock.tryLock(timeout_ms):
        INSTANCE_LOCK = lock
        return True

    return False

# ------------------- Signals -------------------
class SignalHandler(QObject):
    """Internal helper."""
    log_signal = Signal(str)
    progress_signal = Signal(float)
    status_signal = Signal(str)
    task_complete_signal = Signal(str, bool, float)
    extract_complete_signal = Signal(list)
    speed_signal = Signal(str)
    current_progress_signal = Signal(dict)
    video_title_signal = Signal(str)
    auth_result_signal = Signal(object)
    website_result_signal = Signal(object)
    public_config_result_signal = Signal(object)
    ad_reward_result_signal = Signal(object)


class AccountAuthDialog(QDialog):
    auth_request_finished = Signal(object)
    """Internal helper."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("账号登录")
        self.setMinimumSize(430, 455)
        self.setModal(True)
        self.result_data = None
        self._auth_request_in_flight = False
        self._active_auth_mode = ""
        self.auth_request_finished.connect(self._handle_auth_request_finished)

        self._apply_dialog_style()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QVBoxLayout()
        header.setSpacing(3)

        badge = QLabel("ACCOUNT")
        badge.setObjectName("authBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedWidth(80)
        header.addWidget(badge, alignment=Qt.AlignLeft)

        title = QLabel("登录账号")
        title.setObjectName("authTitle")
        header.addWidget(title)

        subtitle = QLabel("登录后将自动同步订阅状态、设备限制与下载权限。")
        subtitle.setObjectName("authSubtitle")
        subtitle.setWordWrap(True)
        header.addWidget(subtitle)

        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setElideMode(Qt.ElideNone)
        layout.addWidget(self.tabs)

        self._build_login_tab()
        self._build_register_tab()
        self._build_reset_password_tab()

        self.feedback_label = QLabel()
        self.feedback_label.setObjectName("authFeedback")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.hide()
        layout.addWidget(self.feedback_label)
        self.tabs.currentChanged.connect(self._clear_feedback)

        tip = QLabel("支持邮箱验证码注册和找回密码；手机号短信验证暂未开放。")
        tip.setObjectName("authTip")
        tip.setWordWrap(True)
        layout.addWidget(tip)

    def _apply_dialog_style(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #08131d;
                color: #e6eef8;
                border: 1px solid #143349;
                border-radius: 14px;
            }
            QLabel#authBadge {
                background: rgba(34, 197, 94, 0.14);
                color: #86efac;
                border: 1px solid rgba(134, 239, 172, 0.25);
                border-radius: 8px;
                padding: 3px 8px;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#authTitle {
                color: #f8fafc;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#authSubtitle {
                color: #94a3b8;
                font-size: 11px;
            }
            QLabel#authTip {
                color: #7dd3fc;
                background: rgba(14, 165, 233, 0.10);
                border: 1px solid rgba(56, 189, 248, 0.16);
                border-radius: 10px;
                padding: 6px 9px;
                font-size: 11px;
            }
            QLabel#authFeedback {
                border-radius: 10px;
                padding: 8px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QTabWidget::pane {
                border: 1px solid #163247;
                background: #0b1823;
                border-radius: 12px;
                top: -1px;
            }
            QTabBar::tab {
                background: transparent;
                color: #8fa4b8;
                padding: 7px 14px;
                margin-right: 8px;
                border: none;
                font-size: 12px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                color: #f8fafc;
                background: rgba(14, 165, 233, 0.15);
                border: 1px solid rgba(56, 189, 248, 0.22);
                border-radius: 10px;
            }
            QFrame#authPanel {
                background: #0b1823;
                border: 1px solid #163247;
                border-radius: 12px;
            }
            QLabel#authLabel {
                color: #CFDDE5;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#authHint {
                color: #64748b;
                font-size: 11px;
            }
            QLineEdit#authInput {
                background: #0f2231;
                border: 1px solid #1e3a50;
                border-radius: 10px;
                color: #f8fafc;
                padding: 7px 10px;
                font-size: 12px;
                min-height: 14px;
            }
            QLineEdit#authInput:focus {
                border: 1px solid #38bdf8;
                background: #102838;
            }
            QPushButton#authPrimary {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0ea5e9, stop:1 #0284c7);
                color: white;
                border: none;
                border-radius: 10px;
                padding: 9px 14px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#authPrimary:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38bdf8, stop:1 #0ea5e9);
            }
            QPushButton#authSecondary {
                background: #123047;
                color: #7dd3fc;
                border: 1px solid #24506d;
                border-radius: 10px;
                padding: 7px 10px;
                font-size: 11px;
                font-weight: 600;
                min-width: 86px;
            }
            QPushButton#authSecondary:hover {
                background: #173b55;
                border-color: #38bdf8;
            }
            QPushButton#authSecondary:disabled {
                color: #64748b;
                background: #102536;
                border-color: #1e3a50;
            }
        """)

    def _build_auth_panel(self, mode_title, mode_desc, button_text, submit_handler):
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        panel = QFrame()
        panel.setObjectName("authPanel")
        content = QVBoxLayout(panel)
        content.setContentsMargins(12, 12, 12, 12)
        content.setSpacing(8)

        heading = QLabel(mode_title)
        heading.setObjectName("authTitle")
        heading.setStyleSheet("font-size:15px;")
        content.addWidget(heading)

        desc = QLabel(mode_desc)
        desc.setObjectName("authSubtitle")
        desc.setWordWrap(True)
        content.addWidget(desc)

        identifier_label = QLabel("账号")
        identifier_label.setObjectName("authLabel")
        content.addWidget(identifier_label)

        identifier_input = QLineEdit()
        identifier_input.setObjectName("authInput")
        identifier_input.setPlaceholderText("请输入邮箱或手机号")
        content.addWidget(identifier_input)

        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(8)

        password_wrap = QVBoxLayout()
        password_wrap.setContentsMargins(0, 0, 0, 0)
        password_wrap.setSpacing(8)

        password_label = QLabel("密码")
        password_label.setObjectName("authLabel")
        password_wrap.addWidget(password_label)

        password_input = QLineEdit()
        password_input.setObjectName("authInput")
        password_input.setEchoMode(QLineEdit.Password)
        password_wrap.addWidget(password_input)

        password_row.addLayout(password_wrap)
        content.addLayout(password_row)

        hint = QLabel("密码建议使用 6 位以上字符。")
        hint.setObjectName("authHint")
        hint.setWordWrap(True)
        content.addWidget(hint)

        action_btn = QPushButton(button_text)
        action_btn.setObjectName("authPrimary")
        action_btn.clicked.connect(submit_handler)
        content.addWidget(action_btn)

        outer.addWidget(panel)
        return tab, identifier_input, password_input, action_btn

    def _insert_verification_code_row(self, tab, send_handler):
        panel = tab.layout().itemAt(0).widget()
        content = panel.layout()

        code_label = QLabel("邮箱验证码")
        code_label.setObjectName("authLabel")

        code_row = QHBoxLayout()
        code_row.setContentsMargins(0, 0, 0, 0)
        code_row.setSpacing(8)

        code_input = QLineEdit()
        code_input.setObjectName("authInput")
        code_input.setPlaceholderText("请输入 6 位验证码")
        code_input.setMaxLength(6)

        send_button = QPushButton("获取验证码")
        send_button.setObjectName("authSecondary")
        send_button.clicked.connect(send_handler)

        code_row.addWidget(code_input, 1)
        code_row.addWidget(send_button)
        content.insertWidget(4, code_label)
        content.insertLayout(5, code_row)
        return code_input, send_button

    def _start_code_cooldown(self, button, seconds=60):
        timer = getattr(button, "_cooldown_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

        button._cooldown_remaining = max(1, int(seconds))
        button.setEnabled(False)
        button.setText(f"{button._cooldown_remaining} 秒")

        timer = QTimer(button)
        button._cooldown_timer = timer

        def tick():
            button._cooldown_remaining -= 1
            if button._cooldown_remaining <= 0:
                timer.stop()
                button.setText("重新获取")
                button.setEnabled(not self._auth_request_in_flight)
                return
            button.setText(f"{button._cooldown_remaining} 秒")

        timer.timeout.connect(tick)
        timer.start(1000)

    def _show_feedback(self, message, kind="info"):
        palette = {
            "success": ("#062b22", "#86efac", "#166534", "操作成功"),
            "error": ("#351319", "#fecdd3", "#9f1239", "操作失败"),
            "info": ("#0b2939", "#bae6fd", "#075985", "提示"),
        }
        background, color, border, heading = palette.get(kind, palette["info"])
        self.feedback_label.setStyleSheet(
            "QLabel#authFeedback {"
            f"background-color: {background};"
            f"color: {color};"
            f"border: 1px solid {border};"
            "border-radius: 10px;"
            "padding: 8px 10px;"
            "font-size: 11px;"
            "font-weight: 600;"
            "}"
        )
        self.feedback_label.setText(f"{heading}：{message}")
        self.feedback_label.show()
        self.feedback_label.raise_()

    def _clear_feedback(self, *_):
        self.feedback_label.clear()
        self.feedback_label.hide()

    def _valid_email_or_warn(self, email):
        email_pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        if not re.match(email_pattern, email or ""):
            self._show_feedback("请输入正确的邮箱地址", "error")
            return False
        return True

    def _set_submit_state(self, button, busy, busy_text):
        if button is None:
            return
        if busy:
            if not hasattr(button, "_default_text"):
                button._default_text = button.text()
            button.setEnabled(False)
            button.setText(busy_text)
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            default_text = getattr(button, "_default_text", "")
            if default_text:
                button.setText(default_text)
            cooldown_remaining = int(getattr(button, "_cooldown_remaining", 0) or 0)
            if cooldown_remaining > 0:
                button.setText(f"{cooldown_remaining} 秒")
                button.setEnabled(False)
            else:
                button.setEnabled(True)
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _set_auth_request_state(self, mode, busy):
        self._auth_request_in_flight = busy
        self._active_auth_mode = mode if busy else ""

        login_busy = busy and mode == "login"
        register_busy = busy and mode == "register"
        reset_busy = busy and mode == "reset_password"
        register_code_busy = busy and mode == "send_register_code"
        reset_code_busy = busy and mode == "send_reset_code"

        self._set_submit_state(self.login_submit_btn, login_busy, "登录中...")
        self._set_submit_state(self.register_submit_btn, register_busy, "注册中...")
        self._set_submit_state(self.reset_submit_btn, reset_busy, "重置中...")
        self._set_submit_state(self.register_send_code_btn, register_code_busy, "发送中...")
        self._set_submit_state(self.reset_send_code_btn, reset_code_busy, "发送中...")

        self.login_identifier.setEnabled(not busy)
        self.login_password.setEnabled(not busy)
        self.register_identifier.setEnabled(not busy)
        self.register_password.setEnabled(not busy)
        self.register_code.setEnabled(not busy)
        self.reset_identifier.setEnabled(not busy)
        self.reset_code.setEnabled(not busy)
        self.reset_password.setEnabled(not busy)
        self.reset_password_confirm.setEnabled(not busy)
        self.tabs.tabBar().setEnabled(not busy)

    def _start_auth_request(self, mode, identifier, password="", verification_code=""):
        if self._auth_request_in_flight:
            return

        self._clear_feedback()
        self._set_auth_request_state(mode, True)

        def worker():
            result = {
                "mode": mode,
                "success": False,
                "msg": "",
                "data": {},
                "title": "操作失败",
            }
            try:
                if mode == "login":
                    success, msg, data = login_account_with_server(identifier, password)
                    result.update({
                        "success": success,
                        "msg": msg or "登录失败",
                        "data": data or {},
                        "title": "登录失败",
                    })
                elif mode == "register":
                    success, msg, register_data = register_account_with_server(
                        identifier,
                        password,
                        verification_code,
                    )
                    if not success:
                        result.update({
                            "success": False,
                            "msg": msg or "注册失败",
                            "data": {},
                            "title": "注册失败",
                        })
                    elif register_data and register_data.get("token"):
                        result.update({
                            "success": True,
                            "msg": msg or "注册成功",
                            "data": register_data,
                            "title": "注册成功",
                        })
                    else:
                        success, msg, data = login_account_with_server(identifier, password)
                        result.update({
                            "success": success,
                            "msg": msg or ("登录失败" if not success else "登录成功"),
                            "data": data or {},
                            "title": "登录失败",
                        })
                elif mode in ("send_register_code", "send_reset_code"):
                    purpose = "register" if mode == "send_register_code" else "reset_password"
                    success, msg, data = send_email_verification_code(identifier, purpose)
                    result.update({
                        "success": success,
                        "msg": msg or ("验证码已发送" if success else "验证码发送失败"),
                        "data": data or {},
                        "title": "验证码发送失败",
                    })
                elif mode == "reset_password":
                    success, msg, data = reset_password_with_email(
                        identifier,
                        verification_code,
                        password,
                    )
                    result.update({
                        "success": success,
                        "msg": msg or ("密码重置成功" if success else "密码重置失败"),
                        "data": data or {},
                        "title": "密码重置失败",
                    })
            except Exception as exc:
                result.update({
                    "success": False,
                    "msg": f"请求异常：{exc}",
                    "data": {},
                    "title": "操作失败",
                })

            self.auth_request_finished.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_auth_request_finished(self, result):
        mode = result.get("mode", "")
        self._set_auth_request_state(mode, False)

        if not result.get("success", False):
            message = result.get("msg", "请求失败")
            self._show_feedback(message, "error")
            return

        if mode in ("send_register_code", "send_reset_code"):
            button = self.register_send_code_btn if mode == "send_register_code" else self.reset_send_code_btn
            retry_after = int((result.get("data") or {}).get("retry_after", 60) or 60)
            self._start_code_cooldown(button, retry_after)
            self._show_feedback("验证码已发送，请检查收件箱或垃圾邮件", "success")
            return

        if mode == "reset_password":
            email = self.reset_identifier.text().strip()
            self.login_identifier.setText(email)
            self.login_password.clear()
            self.reset_code.clear()
            self.reset_password.clear()
            self.reset_password_confirm.clear()
            self.tabs.setCurrentIndex(0)
            self._show_feedback(
                result.get("msg", "密码重置成功，请使用新密码登录"),
                "success",
            )
            return

        self._complete_success(result.get("data", {}))

    def reject(self):
        if self._auth_request_in_flight:
            return
        super().reject()

    def _complete_success(self, data):
        self.result_data = {"mode": "account", "data": data}
        self.done(QDialog.Accepted)

    def _build_login_tab(self):
        tab, self.login_identifier, self.login_password, self.login_submit_btn = self._build_auth_panel(
            "欢迎回来",
            "登录已有账号，快速恢复订阅权益和设备绑定状态。",
            "立即登录",
            self._do_login,
        )
        self.login_password.setPlaceholderText("请输入登录密码")
        self.tabs.addTab(tab, "账号登录")

    def _build_register_tab(self):
        tab, self.register_identifier, self.register_password, self.register_submit_btn = self._build_auth_panel(
            "快速创建账号",
            "获取邮箱验证码并设置密码，注册成功后自动登录。",
            "注册并登录",
            self._do_register,
        )
        self.register_identifier.setPlaceholderText("请输入邮箱地址")
        self.register_code, self.register_send_code_btn = self._insert_verification_code_row(
            tab,
            self._do_send_register_code,
        )
        self.register_password.setPlaceholderText("至少 6 位密码")
        self.tabs.addTab(tab, "快速注册")

    def _build_reset_password_tab(self):
        tab, self.reset_identifier, self.reset_password, self.reset_submit_btn = self._build_auth_panel(
            "找回账号密码",
            "使用注册邮箱获取验证码，然后设置新的登录密码。",
            "确认重置密码",
            self._do_reset_password,
        )
        self.reset_identifier.setPlaceholderText("请输入注册邮箱")
        self.reset_code, self.reset_send_code_btn = self._insert_verification_code_row(
            tab,
            self._do_send_reset_code,
        )
        self.reset_password.setPlaceholderText("请输入至少 6 位新密码")

        panel = tab.layout().itemAt(0).widget()
        content = panel.layout()
        confirm_label = QLabel("确认新密码")
        confirm_label.setObjectName("authLabel")
        self.reset_password_confirm = QLineEdit()
        self.reset_password_confirm.setObjectName("authInput")
        self.reset_password_confirm.setEchoMode(QLineEdit.Password)
        self.reset_password_confirm.setPlaceholderText("请再次输入新密码")
        content.insertWidget(7, confirm_label)
        content.insertWidget(8, self.reset_password_confirm)
        self.tabs.addTab(tab, "找回密码")

    def _do_login(self):
        identifier = self.login_identifier.text().strip()
        password = self.login_password.text()
        if not identifier or not password:
            self._show_feedback("请输入账号和密码", "error")
            return

        self._start_auth_request("login", identifier, password)

    def _do_register(self):
        identifier = self.register_identifier.text().strip()
        password = self.register_password.text()
        verification_code = self.register_code.text().strip()
        if not identifier or not verification_code or not password:
            self._show_feedback("请输入邮箱、验证码和密码", "error")
            return
        if not self._valid_email_or_warn(identifier):
            return
        if not re.match(r"^\d{6}$", verification_code):
            self._show_feedback("请输入 6 位邮箱验证码", "error")
            return
        if len(password) < 6:
            self._show_feedback("密码至少需要 6 位", "error")
            return

        self._start_auth_request("register", identifier, password, verification_code)

    def _do_send_register_code(self):
        email = self.register_identifier.text().strip()
        if not self._valid_email_or_warn(email):
            return
        self._start_auth_request("send_register_code", email)

    def _do_send_reset_code(self):
        email = self.reset_identifier.text().strip()
        if not self._valid_email_or_warn(email):
            return
        self._start_auth_request("send_reset_code", email)

    def _do_reset_password(self):
        email = self.reset_identifier.text().strip()
        verification_code = self.reset_code.text().strip()
        password = self.reset_password.text()
        password_confirm = self.reset_password_confirm.text()
        if not email or not verification_code or not password or not password_confirm:
            self._show_feedback("请完整填写邮箱、验证码和新密码", "error")
            return
        if not self._valid_email_or_warn(email):
            return
        if not re.match(r"^\d{6}$", verification_code):
            self._show_feedback("请输入 6 位邮箱验证码", "error")
            return
        if len(password) < 6:
            self._show_feedback("新密码至少需要 6 位", "error")
            return
        if password != password_confirm:
            self._show_feedback("两次输入的新密码不一致", "error")
            return
        self._start_auth_request("reset_password", email, password, verification_code)


# ------------------- Config Manager -------------------
class ConfigManager:
    """Internal helper."""
    PATH_KEYS = {
        "cookie_file",
        "cookie_instagram",
        "cookie_tiktok",
        "cookie_twitter",
        "deno_path",
        "last_preview_file",
        "last_youtube_preview_file",
    }
    
    def __init__(self):
        self.config_file = CONFIG_FILE
        self.defaults = {
            "language": "zh",
            "theme": "dark",
            "download_path": os.path.expanduser("~/Downloads"),
            "max_threads": int(get_app_value("client.defaults.max_threads", 3)),
            "retry_count": int(get_app_value("client.defaults.retry_count", 3)),
            "cookie_file": "cookies.txt",
            "cookie_instagram": "instagram_cookies.txt",
            "cookie_tiktok": "tiktok_cookies.txt",
            "cookie_twitter": "twitter_cookies.txt",
            "download_type": "video",
            "deno_path": DENO_NAME,
            "deno_timeout": int(get_app_value("client.defaults.deno_timeout", 12)),
            "youtube_visitor_data": "",
            "youtube_po_token": "",
            "youtube_po_token_context": "web.gvs",
            "youtube_advanced_extractor_args": "",
            "youtube_advanced_auth_enabled": True,
            "youtube_format_fallback": True,
            "youtube_user_agent": "",
            "youtube_use_browser_user_agent": True,
            "last_preview_file": "",
            "last_youtube_preview_file": "",
        }
        self.config = self.load_config()

    def _default_runtime_path(self, key):
        default_value = self.defaults.get(key, "")
        if default_value and not os.path.isabs(default_value):
            return os.path.join(BASE_DIR, default_value)
        return default_value

    def _normalize_path_value_for_storage(self, key, value):
        if key not in self.PATH_KEYS:
            return value

        value = (value or "").strip()
        if not value:
            return ""

        if os.path.isabs(value):
            try:
                rel_path = os.path.relpath(value, BASE_DIR)
                if not rel_path.startswith('..') and not os.path.isabs(rel_path):
                    return rel_path
            except Exception:
                pass
        return value

    def _resolve_runtime_path(self, key, value):
        if key not in self.PATH_KEYS:
            return value

        value = (value or "").strip()
        default_runtime = self._default_runtime_path(key)

        if not value:
            if key == "deno_path" and default_runtime and os.path.exists(default_runtime):
                return default_runtime
            return ""

        runtime_path = value if os.path.isabs(value) else os.path.join(BASE_DIR, value)
        if os.path.exists(runtime_path):
            return runtime_path

        if key == "deno_path" and default_runtime and os.path.exists(default_runtime):
            return default_runtime
        return ""
    
    def load_config(self):
        """Internal helper."""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    for key, value in self.defaults.items():
                        if key not in config:
                            config[key] = value
                    for key in self.PATH_KEYS:
                        config[key] = self._normalize_path_value_for_storage(key, config.get(key, self.defaults.get(key, "")))
                    return config
        except Exception:
            pass
        return self.defaults.copy()
    
    def save_config(self):
        """Internal helper."""
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception:
            return False
    
    def get(self, key, default=None):
        """Internal helper."""
        value = self.config.get(key, default)
        if key in self.PATH_KEYS:
            return self._resolve_runtime_path(key, value)
        return value
    
    def set(self, key, value):
        """Internal helper."""
        if key in self.PATH_KEYS:
            value = self._normalize_path_value_for_storage(key, value)
        self.config[key] = value
        return self.save_config()

#
class AnimatedProgressBar(QProgressBar):
    """Internal helper."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.speed_text = ""
        self.speed_history = deque(maxlen=10)
        self.animation_offset = 0
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_animation)
        self.animation_timer.start(50)
        
        #
        self.setStyleSheet("""
            QProgressBar {
                border: 1px solid #CFDDE5;
                border-radius: 6px;
                background-color: #E2E8F0;
                text-align: center;
                color: #0F172A;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #111827;
                border-radius: 6px;
            }
        """)
        
    def set_speed(self, speed_text):
        """Internal helper."""
        self.speed_text = speed_text
        if speed_text:
            self.speed_history.append(speed_text)
        self.update()
        
    def update_animation(self):
        """Internal helper."""
        self.animation_offset = (self.animation_offset + 2) % 20
        self.update()
        
    def paintEvent(self, event):
        """Internal helper."""
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        #
        if self.value() > 0 and self.value() < 100:
            #
            gradient = QLinearGradient(0, 0, self.width(), 0)
            gradient.setColorAt(0, QColor(23, 162, 184, 0))
            gradient.setColorAt(0.2, QColor(23, 162, 184, 100))
            gradient.setColorAt(0.8, QColor(23, 162, 184, 100))
            gradient.setColorAt(1, QColor(23, 162, 184, 0))
            
            #
            chunk_width = int(self.width() * (self.value() / 100.0))
            if chunk_width > 0:
                #
                offset = self.animation_offset
                glow_rect = QRect(offset - 20, 0, 40, self.height())
                glow_rect = glow_rect.intersected(QRect(0, 0, chunk_width, self.height()))
                
                if not glow_rect.isEmpty():
                    painter.fillRect(glow_rect, QBrush(gradient))

# ------------------- Main Window -------------------
class VideoDownloader(QMainWindow):
    """Internal helper."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(800, 600)
        self._init_paths()
        self._init_state()
        self._init_signals()
        self._load_cached_authorization()
        self._ui_initialized = False
        self._build_ui()
        self._ui_initialized = True
        self._show_pending_auth_notice()
        self._apply_styles()
        self._set_window_icon()
        self._apply_windows_title_bar_theme()
        self._refresh_about_page()
        self._start_timers()
        self._initial_check_tools_and_cookie()
        QTimer.singleShot(300, self._load_public_config)
        QTimer.singleShot(500, lambda: self._start_authorization_check(force_refresh=True))
        self.update_manager = AutoUpdateManager(self, self.log_handler)
        if get_app_value("client.update.check_on_start", True):
            QTimer.singleShot(1800, lambda: self.update_manager.check_for_updates(silent=True))

    def _init_paths(self):
        """Internal helper."""
        self.yt_dlp_path = os.path.join(BASE_DIR, YT_DLP_NAME)
        self.ffmpeg_path = os.path.join(BASE_DIR, FFMPEG_NAME)
        self.ffprobe_path = os.path.join(BASE_DIR, FFPROBE_NAME)
        self.deno_path = os.path.join(BASE_DIR, DENO_NAME)
        self.cookie_file = DEFAULT_COOKIE_FILE
        self.instagram_cookie_file = INSTAGRAM_COOKIE_FILE
        self.tiktok_cookie_file = TIKTOK_COOKIE_FILE
        self.twitter_cookie_file = TWITTER_COOKIE_FILE
        self.cache_dir = os.path.join(BASE_DIR, "cache")
        self.logs_dir = os.path.join(BASE_DIR, "logs")

    def _init_state(self):
        """Internal helper."""
        self.config = ConfigManager()
        self.language = self.config.get("language", "zh")
        self.theme = self.config.get("theme", "dark")
        
        self.cookie_file = self.config.get("cookie_file", "") or ""
        self.instagram_cookie_file = self.config.get("cookie_instagram", "") or ""
        self.tiktok_cookie_file = self.config.get("cookie_tiktok", "") or ""
        self.twitter_cookie_file = self.config.get("cookie_twitter", "") or ""
        
        self.download_type = "video"
        
        #
        self.deno_path = os.path.join(BASE_DIR, DENO_NAME)
        self.deno_timeout = self.config.get("deno_timeout", 12)
        self.enable_deno = os.path.exists(self.deno_path)
        
        #
        self.cookie_status = {
            'youtube': CookieHealthManager.empty_status('youtube', self.cookie_file),
            'instagram': CookieHealthManager.empty_status('instagram', self.instagram_cookie_file),
            'tiktok': CookieHealthManager.empty_status('tiktok', self.tiktok_cookie_file),
            'twitter': CookieHealthManager.empty_status('twitter', self.twitter_cookie_file),
        }
        
        self.download_queue = []
        self.active_download_reservation_token = ""
        self.active_downloads = {}
        self.queue_lock = threading.Lock()
        self.task_progress = {}
        self.task_status = {}
        self.max_threads = self.config.get("max_threads", 3)
        
        #
        self.total_tasks = 0
        self.completed_tasks = 0
        self.failed_tasks = 0
        
        self.expire_date = ""
        self.authorized = False
        self.auth_status = ""
        self.auth_msg = ""
        self.auth_mode = "account"
        self.account_info = {}
        self._auth_notice_shown = False
        self.is_downloading = False
        
        self.sidebar_width_expanded = 96
        self.sidebar_width_collapsed = 54
        self.sidebar_margin = 6
        self.sidebar_item_height = 34
        self.sidebar_item_spacing = 5
        self.sidebar_expanded = True
        
        self.current_speed = "0 KB/s"
        self.download_speeds = {}
        self.speed_timer = None
        
        self.current_video_title = "等待下载..."
        self.current_video_progress = 0
        self.current_downloaded_size = "0 B"
        self.current_total_size = "0 B"
        self.current_eta = "00:00"
        self.current_display_url = None
        self.current_progress_url = None
        self._ad_reward_token = ""
        self._ad_reward_polling = False
        self._ad_reward_timer = None
        self._ad_reward_enabled = False
        
        #
        self.initial_check_done = False
        
        #
        self.is_always_on_top = False

    def _init_signals(self):
        """Internal helper."""
        self.signals = SignalHandler()
        self.signals.log_signal.connect(self._append_log)
        self.signals.progress_signal.connect(self._set_progress)
        self.signals.status_signal.connect(self._set_status)
        self.signals.task_complete_signal.connect(self._on_task_complete)
        self.signals.extract_complete_signal.connect(self._on_extract_complete)
        self.signals.speed_signal.connect(self._update_speed)
        self.signals.current_progress_signal.connect(self._update_current_progress)
        self.signals.video_title_signal.connect(self._update_video_title)
        self.signals.auth_result_signal.connect(self._on_authorization_result)
        self.signals.website_result_signal.connect(self._open_resolved_website)
        self.signals.public_config_result_signal.connect(self._handle_public_config_result)
        self.signals.ad_reward_result_signal.connect(self._handle_ad_reward_result)
        
        #
        self.log_handler = LogHandler(BASE_DIR, self._append_log)

    def _initial_check_tools_and_cookie(self):
        """Internal helper."""
        if self.initial_check_done:
            return
            
        self.log_handler.log("启动时检查工具和 Cookie 状态...")
        
        #
        self.yt_dlp_path = os.path.join(BASE_DIR, YT_DLP_NAME)
        self.ffmpeg_path = os.path.join(BASE_DIR, FFMPEG_NAME)
        self.ffprobe_path = os.path.join(BASE_DIR, FFPROBE_NAME)
        self.deno_path = os.path.join(BASE_DIR, DENO_NAME)
        self.enable_deno = bool(self.deno_path and os.path.exists(self.deno_path))
        
        #
        missing_tools = []
        
        if not os.path.exists(self.yt_dlp_path):
            missing_tools.append(f"yt-dlp.exe")
        
        if not os.path.exists(self.ffmpeg_path):
            missing_tools.append("ffmpeg.exe")
        
        #
        if self.enable_deno:
            self.log_handler.log("视频解析运行环境已就绪")
        else:
            self.log_handler.log("视频解析运行环境未就绪：未找到 deno.exe")
        
        if missing_tools:
            tool_list = "\n".join(missing_tools)
            self.log_handler.log(f"工具丢失:\n{tool_list}")
            self.log_handler.log("yt-dlp 下载地址: https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe")
            self.log_handler.log("ffmpeg 下载地址: https://www.gyan.dev/ffmpeg/builds/")
        else:
            self.log_handler.log("所有必要工具已就绪")
        
        #
        self._check_cookie_status_once()
        
        self.initial_check_done = True

    def _append_log(self, text):
        """Internal helper."""
        try:
            if hasattr(self, 'log_box_run') and self.log_box_run is not None:
                self.log_box_run.append(text)
                self.log_box_run.moveCursor(QTextCursor.End)

            for page_name in (
                'page_video_download',
                'page_youtube_download',
                'page_tiktok_download',
                'page_instagram_download',
                'page_twitter_download',
            ):
                page = getattr(self, page_name, None)
                log_box = getattr(page, 'download_log_box', None)
                if log_box is not None:
                    log_box.append(text)
                    log_box.moveCursor(QTextCursor.End)

            if hasattr(self, 'log_box_run') and self.log_box_run is not None:
                self._update_log_info()
        except Exception:
            pass

    def _set_progress(self, val):
        """Internal helper."""
        v = max(0, min(100, int(val)))
        try:
            for page_name in (
                'page_video_download',
                'page_youtube_download',
                'page_tiktok_download',
                'page_instagram_download',
                'page_twitter_download',
            ):
                page = getattr(self, page_name, None)
                if page is not None and hasattr(page, 'lbl_download_percent'):
                    page.lbl_download_percent.setText(f"下载百分比：{v:.1f}%")
        except Exception:
            pass

    def _set_status(self, text):
        """Internal helper."""
        try:
            self.statusBar().showMessage(text)
        except Exception:
            pass
    
    def _update_speed(self, speed_text):
        """Internal helper."""
        self.current_speed = speed_text
        for page_name in (
            'page_video_download',
            'page_youtube_download',
                'page_tiktok_download',
                'page_instagram_download',
                'page_twitter_download',
            ):
            page = getattr(self, page_name, None)
            if page is not None:
                page.update_speed_display(speed_text)
    
    def _update_current_progress(self, progress_info):
        """Internal helper."""
        try:
            progress_type = progress_info.get("type")
            progress_url = progress_info.get("url")
            if progress_type in ("start", "reset"):
                self.current_progress_url = progress_url

            if progress_info.get("type") == "title":
                title = progress_info.get("data", "未知视频")
                if title and title != self.current_video_title:
                    self.current_video_title = title
                    self.log_handler.log(f"[{datetime.now().strftime('%H:%M:%S')}] 当前下载视频：{title}")

            if progress_info.get("type") == "progress":
                data = progress_info.get("data") or {}
                url = progress_info.get("url")
                percent = float(data.get("percent", 0) or 0)
                if url:
                    self.current_progress_url = url
                    with self.queue_lock:
                        if url in self.task_progress:
                            self.task_progress[url] = max(0.0, min(100.0, percent))

                speed = data.get("speed")
                if speed:
                    self.current_speed = speed
        except Exception:
            pass

        for page_name in (
            'page_video_download',
            'page_youtube_download',
            'page_tiktok_download',
            'page_instagram_download',
            'page_twitter_download',
        ):
            page = getattr(self, page_name, None)
            if page is not None:
                page.update_current_progress(progress_info)
    
    def _update_video_title(self, title):
        """Internal helper."""
        for page_name in (
            'page_video_download',
            'page_youtube_download',
            'page_tiktok_download',
            'page_instagram_download',
            'page_twitter_download',
        ):
            page = getattr(self, page_name, None)
            if page is not None:
                page.update_video_title(title)

    def _on_task_complete(self, url, success, progress):
        """Internal helper."""
        try:
            reservation_token = getattr(self, "active_download_reservation_token", "")
            if reservation_token:
                self._settle_account_download_quota(reservation_token, success)

            with self.queue_lock:
                if url in self.active_downloads:
                    del self.active_downloads[url]
                
                if success:
                    self.completed_tasks += 1
                    self.task_progress[url] = 100.0
                    latest_file = self._find_latest_media_file(getattr(self, "last_save_path", ""))
                    if latest_file:
                        self._set_latest_preview_file(latest_file)
                        self.log_handler.log(f"预览文件已就绪: {latest_file}")
                else:
                    self.failed_tasks += 1
                    self.task_progress[url] = 0.0

            if url == self.current_progress_url:
                self.signals.current_progress_signal.emit({
                    "type": "complete",
                    "url": url,
                    "data": {"success": bool(success)},
                })
                self.current_progress_url = None
            
            self._update_task_stats()
            self.update_cookie_status_display()
            if not success:
                self._notify_invalid_youtube_cookie_if_needed()
            
        except Exception as e:
            self.log_handler.log(f"任务状态更新失败: {e}")

    def _find_latest_media_file(self, folder):
        if not folder or not os.path.isdir(folder):
            return ""
        media_exts = (".mp4", ".webm", ".mkv", ".mov")
        candidates = []
        try:
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if os.path.isfile(path) and name.lower().endswith(media_exts):
                    candidates.append((os.path.getmtime(path), path))
        except Exception:
            return ""
        if not candidates:
            return ""
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _set_latest_preview_file(self, file_path):
        """Share the latest downloaded media file with every preview-capable page."""
        if not file_path or not os.path.exists(file_path):
            return
        self.config.set("last_preview_file", file_path)
        self.config.set("last_youtube_preview_file", file_path)
        for page_name in (
            "page_video_download",
            "page_youtube_download",
            "page_tiktok_download",
            "page_instagram_download",
            "page_twitter_download",
            "page_batch_extract",
        ):
            page = getattr(self, page_name, None)
            if page is not None and hasattr(page, "set_preview_file"):
                page.set_preview_file(file_path)

    def _restore_last_preview(self):
        """Restore the last downloaded preview file after app restart."""
        try:
            latest_file = self.config.get("last_preview_file", "") or self.config.get("last_youtube_preview_file", "")
            if latest_file and os.path.exists(latest_file):
                self._set_latest_preview_file(latest_file)
                self.log_handler.log(f"已恢复上次预览文件: {latest_file}")
        except Exception as e:
            self.log_handler.log(f"恢复预览文件失败: {e}")

    def _reset_current_progress(self):
        """Internal helper."""
        for page_name in (
            'page_video_download',
            'page_youtube_download',
            'page_tiktok_download',
            'page_instagram_download',
            'page_twitter_download',
        ):
            page = getattr(self, page_name, None)
            if page is not None:
                page.reset_current_progress()

    def _notify_invalid_youtube_cookie_if_needed(self):
        """Show a direct warning when YouTube marks the cookie as invalid."""
        try:
            status = (self.cookie_status or {}).get("youtube", {})
            if not status or status.get("last_failure_reason") != "AUTH_COOKIE_INVALID":
                return

            failure_marker = (
                status.get("last_failure_at")
                or status.get("last_success_at")
                or str(status.get("consecutive_failures", ""))
            )
            current_marker = f"youtube_cookie_invalid:{failure_marker}"
            if getattr(self, "_last_cookie_warning_marker", "") == current_marker:
                return

            self._last_cookie_warning_marker = current_marker
            QMessageBox.warning(
                self,
                "YouTube Cookie 已失效",
                "这份 YouTube Cookie 已被平台判定为失效，请重新导出并重新导入后再下载。",
            )
        except Exception:
            pass

    def _on_extract_complete(self, extracted_data):
        """Internal helper."""
        page = getattr(self, 'page_batch_extract', None)
        if page is not None:
            page._on_extract_complete(extracted_data)

    def _apply_authorization_result(self, auth, log_result=True, show_notice=False):
        self.authorized = auth.get("valid", False)
        self.expire_date = auth.get("expire_date", "")
        self.auth_status = auth.get("status", "")
        self.auth_msg = auth.get("msg", "")
        self.auth_mode = "account"
        self.account_info = auth.get("account", {})

        if log_result and self.authorized:
            level = self.account_info.get("account_level_label", "账号订阅")
            remain = self.account_info.get("today_download_remaining", "")
            remain_text = ""
            try:
                if remain != "" and remain is not None and int(remain) >= 0:
                    if self.account_info.get("quota_mode") == "credit":
                        remain_text = f"，免费额度剩余 {remain} 次"
                    else:
                        remain_text = f"，今日剩余 {remain} 次"
            except Exception:
                pass
            self.log_handler.log(f"账号验证成功（{level}{remain_text}）")
        elif log_result:
            self.log_handler.log(f"⚠️ {self.auth_msg}")
        self._update_login_button_text()
        self._refresh_about_page()
        if show_notice:
            self._show_pending_auth_notice()

    def _is_account_subscription_expired(self):
        status = str(getattr(self, "auth_status", "") or "").strip()
        msg = str(getattr(self, "auth_msg", "") or "").strip()
        if status == "expired" or msg == "subscription_expired":
            return True
        expire_date = getattr(self, "expire_date", "") or self.account_info.get("expire_date", "")
        if not expire_date:
            return False
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(expire_date), fmt) < datetime.now()
            except Exception:
                continue
        return False

    def _load_cached_authorization(self):
        """Read only lightweight local auth data before the window appears."""
        auth_data = load_auth_data()
        self.auth_mode = "account"
        self.account_info = auth_data
        self.authorized = False
        self.expire_date = auth_data.get("expire_date", "")
        self.auth_status = "pending" if auth_data.get("token") else "no_local"
        self.auth_msg = "账号状态正在后台验证" if self.auth_status == "pending" else "未登录"
        self._update_login_button_text()

    def _start_authorization_check(self, force_refresh=False):
        """Refresh authorization away from the UI thread."""
        def worker():
            try:
                auth = ensure_authorized(force_refresh=force_refresh)
            except Exception as exc:
                auth = {
                    "valid": False,
                    "expire_date": "",
                    "msg": f"状态验证异常：{exc}",
                    "status": "error",
                    "auth_mode": "account",
                    "account": self.account_info,
                }
            self.signals.auth_result_signal.emit(auth)

        threading.Thread(target=worker, daemon=True).start()

    def _on_authorization_result(self, auth):
        self._apply_authorization_result(auth, log_result=True, show_notice=True)

    def _authorization_check(self, force_refresh=False):
        """Internal helper."""
        auth = ensure_authorized(force_refresh=force_refresh)
        self._apply_authorization_result(auth, log_result=True, show_notice=True)
        return auth

    def _show_pending_auth_notice(self):
        if getattr(self, "auth_status", "") == "device_mismatch" and getattr(self, "auth_msg", ""):
            if getattr(self, "_auth_notice_shown", False):
                return
            self._auth_notice_shown = True
            QMessageBox.warning(self, "账号已下线", self.auth_msg)

    def _update_login_button_text(self):
        if not hasattr(self, "btn_account_login"):
            return
        if self.authorized or load_auth_data().get("token"):
            self.btn_account_login.setText("退出")
            self.btn_account_login.setToolTip("退出当前账号并刷新状态")
        else:
            self.btn_account_login.setText("账号登录")
            self.btn_account_login.setToolTip("登录账号并同步订阅状态")

    def _apply_account_login_state(self, data):
        status = data.get("status", "ok")
        if status in (1, "1", True, "", None):
            status = "ok"
        self.authorized = bool(data.get("valid", status == "ok"))
        self.auth_mode = "account"
        self.auth_status = status
        self.auth_msg = data.get("msg", "登录成功")
        self.expire_date = data.get("expire_date", "")
        self.account_info = data or {}
        if self._is_account_subscription_expired():
            self.account_info["account_level_label"] = "订阅已过期"
            self.account_info["today_download_remaining"] = 0
        self._update_login_button_text()
        self._refresh_about_page()
        QApplication.processEvents()

    def _consume_account_download_quota(self, url_count, source_label="下载任务"):
        normalized_count = max(1, int(url_count or 1))
        self.log_handler.log(f"账号模式下载校验：{source_label}，准备预占 {normalized_count} 个下载名额")

        permission = reserve_download_permission(normalized_count)
        self.authorized = permission.get("valid", False)
        self.expire_date = permission.get("expire_date", "")
        self.auth_status = permission.get("status", "")
        self.auth_msg = permission.get("msg", "")
        self.account_info = permission.get("account", self.account_info)

        self.log_handler.log(f"账号模式下载校验结果：status={self.auth_status}, msg={self.auth_msg}")

        if not self.authorized:
            if self.auth_status == "task_limit_exceeded":
                task_limit = self.account_info.get("per_task_limit", 1)
                QMessageBox.warning(
                    self,
                    "单次下载数量超限",
                    f"当前套餐每次最多下载 {task_limit} 个视频。\n"
                    f"本次提交了 {normalized_count} 个，请减少后重试。",
                )
            elif self.auth_status == "quota_exceeded":
                remain = self.account_info.get("today_download_remaining", 0)
                is_credit = self.account_info.get("quota_mode") == "credit"
                QMessageBox.warning(
                    self,
                    "免费额度不足" if is_credit else "今日下载额度不足",
                    (
                        (
                            f"免费额度已用完。\n当前剩余 {remain} 次，"
                            + (
                                "可点击顶部“免费领取额度”前往官网领取，或购买付费订阅。"
                                if self._ad_reward_enabled
                                else "免费领取功能暂未开放，可选择付费订阅套餐。"
                            )
                        )
                        if is_credit
                        else f"{self.auth_msg}\n今日剩余可下载 {remain} 个视频。"
                    ),
                )
            else:
                QMessageBox.warning(self, "账号权限不足", self.auth_msg or "账号权限校验失败")
            self._refresh_about_page()
            return ""

        self._refresh_about_page()
        return permission.get("reservation_token", "")

    def _settle_account_download_quota(self, reservation_token, success, settled_count=1):
        if not reservation_token:
            return False
        result = settle_download_permission(
            reservation_token,
            bool(success),
            settled_count=max(1, int(settled_count or 1)),
        )
        if result.get("account"):
            self.account_info = result["account"]
            self._refresh_about_page()
        if result.get("valid"):
            outcome = "成功并扣除次数" if success else "失败且未扣除次数"
            self.log_handler.log(f"下载额度结算：{outcome}")
            return True
        self.log_handler.log(
            f"下载额度结算失败：status={result.get('status', '')}, "
            f"msg={result.get('msg', '')}"
        )
        return False


    def _set_window_icon(self):
        """Set the app icon used by the title bar and Windows taskbar."""
        try:
            if os.path.exists(ICON_FILE):
                app_icon = QIcon(ICON_FILE)
            else:
                jpg_icon = os.path.join(BASE_DIR, "icon.jpg")
                app_icon = QIcon(jpg_icon) if os.path.exists(jpg_icon) else QIcon()

            if not app_icon.isNull():
                self.setWindowIcon(app_icon)
                QApplication.setWindowIcon(app_icon)
        except Exception as e:
            self.log_handler.log(f"设置窗口图标失败: {e}")

    def _apply_windows_title_bar_theme(self):
        """Apply the app theme color to the native Windows title bar."""
        if os.name != "nt":
            return

        try:
            hwnd = int(self.winId())
            caption_color = ctypes.c_int(0x00445C17)
            text_color = ctypes.c_int(0x00FFFFFF)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color))
        except Exception:
            pass

    #
    def _build_ui(self):
        """Internal helper."""
        #
        self.setWindowFlags(Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint)
        
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        #
        top = QFrame()
        top.setObjectName("topbar")
        top.setFixedHeight(64)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(12, 6, 10, 6)
        top_layout.setSpacing(4)

        self.btn_toggle_sidebar = UIComponents.create_button("☰", 36, 20)
        self.btn_toggle_sidebar.setFixedWidth(20)
        self.btn_toggle_sidebar.setFocusPolicy(Qt.NoFocus)
        self.btn_toggle_sidebar.clicked.connect(self.toggle_sidebar)
        self.btn_toggle_sidebar.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                text-align: center;
                font-size: 18px;
                font-weight: 700;
                color: #17445C;
                min-width: 20px;
                width: 20px;
                height: 24px;
            }
            QPushButton:hover {
                background: transparent;
                color: #2E7892;
            }
        """)
        top_layout.addWidget(self.btn_toggle_sidebar)

        title_auth_layout = QHBoxLayout()
        title_auth_layout.setContentsMargins(0, 0, 0, 0)
        title_auth_layout.setSpacing(0)
        logo_path = os.path.join(BASE_DIR, "logo.png")
        if os.path.exists(logo_path):
            self.lbl_title = QLabel()
            pixmap = QPixmap(logo_path)
            size = min(pixmap.width(), pixmap.height())
            pixmap = pixmap.copy((pixmap.width() - size) // 2, (pixmap.height() - size) // 2, size, size)
            pixmap = pixmap.scaled(40, 40, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            mask = QPixmap(40, 40)
            mask.fill(Qt.transparent)
            painter = QPainter(mask)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(Qt.white)
            painter.drawEllipse(0, 0, 40, 40)
            painter.end()
            pixmap.setMask(mask.mask())
            self.lbl_title.setPixmap(pixmap)
            self.lbl_title.setToolTip(get_app_value("client.app_tag", WINDOW_TITLE))
        else:
            self.lbl_title = UIComponents.create_label(get_app_value("client.app_tag", WINDOW_TITLE), "font-weight:700; font-size:15px;")

        self.combo_language = QComboBox()
        self.combo_language.addItem("中文", "zh")
        self.combo_language.addItem("English", "en")
        self.combo_language.setCurrentIndex(0 if self.language == "zh" else 1)
        self.combo_language.currentIndexChanged.connect(self.on_language_changed)
        self.combo_language.hide()

        self.lbl_title.setContentsMargins(0, 0, 0, 0)
        self.lbl_title.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        title_auth_layout.addWidget(self.lbl_title)

        self.btn_language = QToolButton()
        self.btn_language.setFocusPolicy(Qt.NoFocus)
        self.btn_language.setPopupMode(QToolButton.InstantPopup)
        self.btn_language.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_language.setAutoRaise(True)
        self.btn_language.setText("中文 ▾" if self.language == "zh" else "English ▾")
        self.btn_language.setStyleSheet("""
            QToolButton {
                background: transparent;
                color:#17445C;
                border:none;
                padding:2px 5px;
                margin:0px;
                font-family: "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif;
                font-size:11px;
                font-weight: 700;
            }
            QToolButton:hover {
                background: transparent;
                color:#2E7892;
            }
            QToolButton::menu-indicator {
                image:none;
                width:0px;
            }
        """)
        self.language_menu = QMenu(self)
        self.language_menu.setStyleSheet("""
            QMenu {
                background-color:#F8FBFC;
                color:#0F172A;
                border:1px solid #CFDDE5;
            }
            QMenu::item:selected {
                background-color:#EAF2F6;
            }
        """)
        action_zh = self.language_menu.addAction("中文")
        action_en = self.language_menu.addAction("English")
        action_zh.triggered.connect(lambda: self.set_language("zh"))
        action_en.triggered.connect(lambda: self.set_language("en"))
        self.btn_language.setMenu(self.language_menu)
        title_auth_layout.addWidget(self.btn_language)
        title_auth_layout.addSpacing(16)

        current_date = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%H:%M:%S")

        time_widget = QWidget()
        time_layout = QHBoxLayout(time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(10)

        self.lbl_date = UIComponents.create_label(f"{current_date}", "font-size:14px; font-weight:bold; color:#17445C;")
        time_layout.addWidget(self.lbl_date)

        self.lbl_time = UIComponents.create_label(f"{current_time}", "font-size:14px; font-weight:bold; color:#17445C;")
        time_layout.addWidget(self.lbl_time)

        self.btn_always_on_top = QPushButton("📌")
        self.btn_always_on_top.setFocusPolicy(Qt.NoFocus)
        self.btn_always_on_top.setObjectName("btn_always_on_top")
        self.btn_always_on_top.setFixedSize(27, 27)
        self.btn_always_on_top.setStyleSheet("""
            QPushButton#btn_always_on_top {
                background: transparent;
                border: none;
                color: #17445C;
                font-size: 15px;
                min-width: 27px;
                height: 27px;
                padding: 0;
                margin: 0;
            }
            QPushButton#btn_always_on_top:hover {
                background: transparent;
                color:#2E7892;
            }
        """)
        self.btn_always_on_top.setToolTip("切换窗口置顶状态")
        self.btn_always_on_top.clicked.connect(self.toggle_always_on_top)
        time_layout.addWidget(self.btn_always_on_top)

        title_auth_layout.addWidget(time_widget)
        title_auth_layout.addStretch()
        top_layout.addLayout(title_auth_layout)

        #
        top_layout.addSpacerItem(QSpacerItem(16, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))

        self.btn_ad_reward_top = QPushButton("免费领取额度")
        self.btn_ad_reward_top.setFocusPolicy(Qt.NoFocus)
        self.btn_ad_reward_top.setObjectName("topRewardButton")
        self.btn_ad_reward_top.setFixedSize(108, 30)
        self.btn_ad_reward_top.setToolTip("前往官网免费领取下载额度")
        self.btn_ad_reward_top.clicked.connect(self.open_ad_reward)
        self.btn_ad_reward_top.setVisible(False)
        top_layout.addWidget(self.btn_ad_reward_top)
        top_layout.addSpacing(6)

        #
        self.btn_website = QPushButton("官网教程")
        self.btn_website.setFocusPolicy(Qt.NoFocus)
        self.btn_website.setObjectName("topNavButton")
        self.btn_website.setFixedHeight(27)
        self.btn_website.setMinimumWidth(58)
        self.btn_website.clicked.connect(self.open_website)
        top_layout.addWidget(self.btn_website)

        #
        self.btn_account_login = QPushButton("账号登录")
        self.btn_account_login.setFocusPolicy(Qt.NoFocus)
        self.btn_account_login.setObjectName("topNavButton")
        self.btn_account_login.setFixedHeight(27)
        self.btn_account_login.setMinimumWidth(58)
        self.btn_account_login.clicked.connect(self.prompt_for_account)

        #
        self.btn_open_folder = QPushButton("打开下载文件")
        self.btn_open_folder.setFocusPolicy(Qt.NoFocus)
        self.btn_open_folder.setObjectName("topNavButton")
        self.btn_open_folder.setFixedHeight(27)
        self.btn_open_folder.setMinimumWidth(76)
        self.btn_open_folder.clicked.connect(self.open_download_folder)
        top_layout.addWidget(self.btn_open_folder)
        top_layout.addWidget(self.btn_account_login)



        root.addWidget(top)

        #
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.pages = QStackedWidget()

        self._lazy_page_factories = {
            1: ("page_video_download", lambda: self._create_video_download_page()),
            2: ("page_youtube_download", lambda: self._create_video_download_page("YouTube")),
            3: ("page_tiktok_download", lambda: self._create_video_download_page("TikTok")),
            4: ("page_instagram_download", lambda: self._create_video_download_page("Instagram")),
            5: ("page_twitter_download", lambda: self._create_video_download_page("Twitter")),
            6: ("page_batch_extract", self._create_batch_extract_page),
            7: ("page_about", self._create_about_page),
        }

        #
        self.page_settings = SettingsPage(self, self.log_handler, self.config)
        self.page_run_log = self._page_run_log_ui()

        self._restore_last_preview()
        
        self.pages.addWidget(self.page_settings)
        self.pages.addWidget(self._page_loading_placeholder("视频下载"))
        self.pages.addWidget(self._page_loading_placeholder("YouTube下载"))
        self.pages.addWidget(self._page_loading_placeholder("TikTok下载"))
        self.pages.addWidget(self._page_loading_placeholder("Instagram下载"))
        self.pages.addWidget(self._page_loading_placeholder("Twitter下载"))
        self.pages.addWidget(self._page_loading_placeholder("主页提取"))
        self.pages.addWidget(self._page_loading_placeholder("账号信息"))
        self.pages.addWidget(self.page_run_log)

        #
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(self.sidebar_width_expanded)
        
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(self.sidebar_margin, 10, self.sidebar_margin, 10)
        side_layout.setSpacing(0)

        self.nav_items = [
            {"text": "账号信息", "icon": "账", "tooltip": "账号信息", "page_index": 7},
            {"text": "设置", "icon": "⚙", "tooltip": "设置", "page_index": 0},
            {"text": "YouTube下载", "icon": "YT", "tooltip": "YouTube视频下载", "page_index": 2},
            {"text": "TikTok下载", "icon": "TK", "tooltip": "TikTok视频下载", "page_index": 3},
            {"text": "Instagram下载", "icon": "IG", "tooltip": "Instagram视频下载", "page_index": 4},
            {"text": "Twitter下载", "icon": "X", "tooltip": "Twitter/X视频下载", "page_index": 5},
            {"text": "主页提取", "icon": "主", "tooltip": "主页提取", "page_index": 6},
            {"text": "日志", "icon": "日", "tooltip": "日志", "page_index": 8},
            {"text": "重启", "icon": "↻", "tooltip": "重启", "page_index": -1},
            {"text": "检查更新", "icon": "更", "tooltip": "检查更新", "page_index": -2}
        ]
        
        for item in self.nav_items:
            if item.get("page_index") == 2:
                item["text"] = "油管"
                item["tooltip"] = "油管"
            elif item.get("page_index") == 3:
                item["text"] = "Tiktok"
                item["tooltip"] = "Tiktok"
            elif item.get("page_index") == 4:
                item["text"] = "Inst"
                item["tooltip"] = "Inst"
            elif item.get("page_index") == 5:
                item["text"] = "Twitter"
                item["tooltip"] = "Twitter/X"

        self.sidebar_width_expanded = self._compute_sidebar_expanded_width()
        side.setFixedWidth(self.sidebar_width_expanded)

        self.nav_buttons = []
        for item in self.nav_items:
            button = QPushButton(item["text"])
            button.setFocusPolicy(Qt.NoFocus)
            button.setObjectName("navButton")
            button.setCheckable(item.get("page_index", 0) >= 0)
            button.setFixedSize(self._sidebar_nav_width(), self.sidebar_item_height)
            button.setToolTip(item["tooltip"])
            button.setProperty("collapsed", False)
            button.clicked.connect(lambda checked=False, nav_item=item, nav_button=button: self._on_nav_button_clicked(nav_item, nav_button))
            self.nav_buttons.append(button)
            side_layout.addWidget(button)
            side_layout.addSpacing(self.sidebar_item_spacing)

        side_layout.addStretch()
        body.addWidget(side)
        body.addWidget(self.pages, stretch=1)
        root.addLayout(body)

        status = QStatusBar()
        self.setStatusBar(status)
        
        #
        self.btn_disclaimer = QPushButton("鍏嶈垂澹版槑")
        self.btn_disclaimer.setText("免责声明")
        self.btn_disclaimer.setFixedHeight(20)
        self.btn_disclaimer.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #334155;
                font-size: 10px;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: rgba(15, 23, 42, 0.08);
                border-radius: 4px;
            }
        """)
        self.btn_disclaimer.clicked.connect(self.open_disclaimer)
        status.addPermanentWidget(self.btn_disclaimer)
        
        self.btn_privacy = QPushButton("闅愮鏀跨瓥")
        self.btn_privacy.setText("隐私政策")
        self.btn_privacy.setFixedHeight(20)
        self.btn_privacy.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #334155;
                font-size: 10px;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: rgba(15, 23, 42, 0.08);
                border-radius: 4px;
            }
        """)
        self.btn_privacy.clicked.connect(self.open_privacy_policy)
        status.addPermanentWidget(self.btn_privacy)
        
        self.btn_terms = QPushButton("使用条款")
        self.btn_terms.setText("使用条款")
        self.btn_terms.setFixedHeight(20)
        self.btn_terms.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #334155;
                font-size: 10px;
                padding: 0 8px;
            }
            QPushButton:hover {
                background: rgba(15, 23, 42, 0.08);
                border-radius: 4px;
            }
        """)
        self.btn_terms.clicked.connect(self.open_terms_of_use)
        status.addPermanentWidget(self.btn_terms)
        
        self.statusBar().showMessage("状态：就绪")

        self._update_sidebar_display()
        #
        if self._ensure_page_loaded(7):
            self.pages.setCurrentIndex(7)
            self._select_nav_button_by_page(7)

    def _page_loading_placeholder(self, title):
        """Small placeholder used until a heavy page is opened for the first time."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addStretch()

        label = QLabel(f"{title} 正在准备...")
        label.setAlignment(Qt.AlignCenter)
        label.setObjectName("lazyPagePlaceholder")
        layout.addWidget(label)

        layout.addStretch()
        return page

    def _replace_lazy_page(self, page_index, page):
        old_page = self.pages.widget(page_index)
        self.pages.removeWidget(old_page)
        old_page.deleteLater()
        self.pages.insertWidget(page_index, page)
        return page

    def _ensure_page_loaded(self, page_index):
        factory_info = getattr(self, "_lazy_page_factories", {}).get(page_index)
        if not factory_info:
            return True

        attr_name, factory = factory_info
        if getattr(self, attr_name, None) is not None:
            return True

        try:
            page = factory()
            setattr(self, attr_name, page)
            self._replace_lazy_page(page_index, page)
            self.update_cookie_status_display()
            return True
        except Exception as e:
            self.log_handler.log(f"加载页面失败: {e}")
            QMessageBox.warning(self, "加载失败", f"页面加载失败: {e}")
            return False

    def _create_video_download_page(self, platform_name=None):
        from videodown import VideoDownloadPage

        if platform_name:
            page = VideoDownloadPage(self, self.log_handler, self.config, platform=platform_name)
        else:
            page = VideoDownloadPage(self, self.log_handler, self.config)
        page.set_signal_handler(self.signals)
        page.set_download_callback(self._start_video_download)

        last_preview_file = self.config.get("last_preview_file", "") or self.config.get("last_youtube_preview_file", "")
        if last_preview_file and os.path.exists(last_preview_file) and hasattr(page, "set_preview_file"):
            page.set_preview_file(last_preview_file)
        return page

    def _create_batch_extract_page(self):
        from piliang import BatchExtractPage

        page = BatchExtractPage(
            self,
            self.config,
            self.yt_dlp_path,
            self.ffmpeg_path,
            self.deno_path,
            self,
            self.log_handler,
        )
        page.set_cookie_files(self.cookie_file, self.instagram_cookie_file, self.tiktok_cookie_file, self.twitter_cookie_file)
        page.signals.extract_complete_signal.connect(self._on_extract_complete)

        last_preview_file = self.config.get("last_preview_file", "") or self.config.get("last_youtube_preview_file", "")
        if last_preview_file and os.path.exists(last_preview_file) and hasattr(page, "set_preview_file"):
            page.set_preview_file(last_preview_file)
        return page

    def _create_about_page(self):
        from about import AboutPage

        page = AboutPage(self, self.config, self.log_handler)
        return page

    def _page_run_log_ui(self):
        """Internal helper."""
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        toolbar = QHBoxLayout()
        
        self.btn_clear_log = UIComponents.create_button("清空日志", 36, 100)
        self.btn_clear_log.clicked.connect(self.clear_run_log)
        toolbar.addWidget(self.btn_clear_log)
        
        self.btn_copy_log = UIComponents.create_button("复制日志", 36, 100)
        self.btn_copy_log.clicked.connect(self.copy_run_log)
        toolbar.addWidget(self.btn_copy_log)
        
        self.btn_save_log = UIComponents.create_button("保存日志", 36, 100)
        self.btn_save_log.clicked.connect(self.save_run_log)
        toolbar.addWidget(self.btn_save_log)
        
        toolbar.addSpacerItem(QSpacerItem(20, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        layout.addLayout(toolbar)
        
        self.log_box_run = QTextEdit()
        self.log_box_run.setReadOnly(True)
        self.log_box_run.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_box_run.setPlaceholderText("运行日志将在此显示...")
        layout.addWidget(self.log_box_run, stretch=1)
        
        log_info_layout = QHBoxLayout()
        self.lbl_log_info = UIComponents.create_label("日志信息: 0 条记录")
        log_info_layout.addWidget(self.lbl_log_info)
        
        log_info_layout.addStretch()
        self.lbl_log_time = UIComponents.create_label(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log_info_layout.addWidget(self.lbl_log_time)
        layout.addLayout(log_info_layout)
        
        return w

    def _handle_page_activation(self, page_index):
        """Internal helper."""
        if not self._ensure_page_loaded(page_index):
            return

        if page_index == 0:
            self.page_settings.set_config(self.config)
            self.page_settings.set_log_handler(self.log_handler)
            self.page_settings.update_cache_info()
            self.page_settings.update_tools_status()
            self.update_cookie_status_display()
        elif page_index == 6:
            self.page_batch_extract.set_config(self.config)
            self.page_batch_extract.set_yt_dlp_path(self.yt_dlp_path)
            self.page_batch_extract.set_cookie_files(self.cookie_file, self.instagram_cookie_file, self.tiktok_cookie_file, self.twitter_cookie_file)
            self.page_batch_extract.deno_path = self.deno_path
            self.page_batch_extract.update_cookie_status_display()
        elif page_index == 7:
            self._refresh_about_page()
            self._start_authorization_check(force_refresh=True)
        elif page_index == 8:
            self._update_log_info()

    def check_for_updates(self, silent=False):
        if not hasattr(self, "update_manager"):
            self.update_manager = AutoUpdateManager(self, self.log_handler)
        self.update_manager.check_for_updates(silent=silent)
    
    def _refresh_about_page(self):
        """Internal helper."""
        self._update_ad_reward_visibility()
        if hasattr(self, 'page_about') and self.page_about:
            auth_info = {
                "valid": self.authorized,
                "expire_date": self.expire_date,
                "status": self.auth_status,
                "msg": self.auth_msg,
                "auth_mode": self.auth_mode,
                "ad_reward_enabled": self._ad_reward_enabled,
                "account": self.account_info,
            }
            self.page_about.update_auth_info(auth_info)
            self.page_about.update_tools_status()

    def _update_log_info(self):
        """Internal helper."""
        try:
            if hasattr(self, 'log_box_run'):
                line_count = self.log_handler.get_log_stats(self.log_box_run)
                self.lbl_log_info.setText(f"日志信息: {line_count} 条记录")
                self.lbl_log_time.setText(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        except Exception:
            pass

    def clear_run_log(self):
        """Internal helper."""
        self.log_handler.clear_run_log(self.log_box_run, self.lbl_log_info, self.lbl_log_time)
        for page_name in (
            'page_video_download',
            'page_youtube_download',
            'page_tiktok_download',
            'page_instagram_download',
        ):
            page = getattr(self, page_name, None)
            log_box = getattr(page, 'download_log_box', None)
            if log_box is not None:
                log_box.clear()

    def copy_run_log(self):
        """Internal helper."""
        self.log_handler.copy_run_log(self.log_box_run)

    def save_run_log(self):
        """Internal helper."""
        self.log_handler.save_run_log(self.log_box_run, self)

    def _compute_sidebar_expanded_width(self):
        """Size the expanded sidebar to fit the longest navigation label."""
        font = self.font()
        metrics = QFontMetrics(font)
        longest_text_width = 0

        for item in getattr(self, 'nav_items', []):
            longest_text_width = max(longest_text_width, metrics.horizontalAdvance(item.get("text", "")))

        return max(92, min(118, longest_text_width + (self.sidebar_margin * 2) + 22))

    def _sidebar_nav_width(self):
        return max(1, self.sidebar_width_expanded - (self.sidebar_margin * 2))

    def toggle_sidebar(self):
        """Internal helper."""
        side = self.findChild(QFrame, "sidebar")
        if not side:
            return

        target_width = (
            self.sidebar_width_collapsed
            if self.sidebar_expanded
            else self.sidebar_width_expanded
        )

        self.sidebar_expanded = not self.sidebar_expanded

        start_width = side.width()
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(220)
        self.anim.setStartValue(start_width)
        self.anim.setEndValue(target_width)
        self.anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.anim.valueChanged.connect(lambda value: side.setFixedWidth(int(value)))
        self.anim.start()

        self._update_sidebar_display()

    def _update_sidebar_display(self):
        """Internal helper."""
        if not hasattr(self, "nav_buttons"):
            return
        width = self._sidebar_nav_width() if self.sidebar_expanded else self.sidebar_width_collapsed - (self.sidebar_margin * 2)
        if not self.sidebar_expanded:
            for button, nav_data in zip(self.nav_buttons, self.nav_items):
                button.setText(nav_data["icon"])
                button.setToolTip(nav_data["tooltip"])
                button.setFixedSize(width, self.sidebar_item_height)
                button.setProperty("collapsed", True)
                button.style().unpolish(button)
                button.style().polish(button)
        else:
            self.btn_website.setText("官网教程")
            self.btn_website.setToolTip("")
            self._update_login_button_text()

            for button, nav_data in zip(self.nav_buttons, self.nav_items):
                button.setText(nav_data["text"])
                button.setToolTip(nav_data["tooltip"])
                button.setFixedSize(width, self.sidebar_item_height)
                button.setProperty("collapsed", False)
                button.style().unpolish(button)
                button.style().polish(button)

    def _select_nav_button_by_page(self, page_index):
        if not hasattr(self, "nav_buttons"):
            return
        for button, nav_data in zip(self.nav_buttons, self.nav_items):
            button.setChecked(nav_data.get("page_index") == page_index)

    def _on_nav_button_clicked(self, nav_data, button):
        page_index = nav_data.get("page_index")
        if page_index == -1:
            button.setChecked(False)
            if not getattr(self, "_ui_initialized", False):
                return
            self.restart_application()
            return
        if page_index == -2:
            button.setChecked(False)
            if not getattr(self, "_ui_initialized", False):
                return
            self.check_for_updates(silent=False)
            return
        if 0 <= page_index < self.pages.count():
            if not self._ensure_page_loaded(page_index):
                button.setChecked(False)
                return
            self.pages.setCurrentIndex(page_index)
            self._handle_page_activation(page_index)
            self._select_nav_button_by_page(page_index)

    def open_account_info_page(self):
        """Open the account info page from the top navigation."""
        try:
            if not self._ensure_page_loaded(7):
                return
            self.pages.setCurrentIndex(7)
            self._handle_page_activation(7)
            self._select_nav_button_by_page(7)
        except Exception as e:
            self.log_handler.log(f"打开账号信息失败: {e}")

    def open_website(self):
        """Internal helper."""
        if not self.btn_website.isEnabled():
            return

        fallback_url = get_app_value("client.website.home", "https://www.muyanshidai.com")
        self.btn_website.setEnabled(False)
        self.btn_website.setText("获取链接...")

        def worker():
            success, data = get_public_site_config(timeout=10)
            self.signals.website_result_signal.emit({
                "success": success,
                "url": str((data or {}).get("subscription_url", "") or "").strip(),
                "fallback_url": fallback_url,
                "msg": str((data or {}).get("msg", "") or "").strip(),
            })

        threading.Thread(target=worker, daemon=True).start()

    def _load_public_config(self):
        """Refresh server-controlled client feature availability."""
        def worker():
            success, data = get_public_site_config(timeout=10)
            self.signals.public_config_result_signal.emit({
                "success": success,
                "data": data or {},
            })

        threading.Thread(target=worker, daemon=True).start()

    def _handle_public_config_result(self, result):
        data = result.get("data", {}) if result.get("success") else {}
        self._ad_reward_enabled = bool(data.get("ad_reward_enabled", False))
        self._update_ad_reward_visibility()
        self._refresh_about_page()

    def _is_ad_reward_account_eligible(self):
        if not self.authorized:
            return True
        account = self.account_info or load_auth_data()
        account_level = str(account.get("account_level", "free")).strip().lower()
        return account_level in ("free", "trial")

    def _update_ad_reward_visibility(self):
        top_button = getattr(self, "btn_ad_reward_top", None)
        if top_button is not None:
            top_button.setVisible(bool(self._ad_reward_enabled))

    def _open_resolved_website(self, result):
        self.btn_website.setEnabled(True)
        self.btn_website.setText("官网教程")

        url = result.get("url", "") if result.get("success") else ""
        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = result.get("fallback_url", "https://www.muyanshidai.com")

        try:
            webbrowser.open(url)
            self.log_handler.log(f"打开官网教程：{url}")
        except Exception as exc:
            self.log_handler.log(f"打开官网失败：{exc}")

    def open_ad_reward(self):
        """Open the public homepage without sending client account state."""
        reward_home_url = "https://license.muyanshidai.com/index.php"
        try:
            webbrowser.open(reward_home_url)
            self.log_handler.log(f"已打开官网免费额度入口：{reward_home_url}")
        except Exception as exc:
            QMessageBox.warning(self, "免费次数", f"无法打开官网领取页面：{exc}")

    def _poll_ad_reward_status(self):
        if self._ad_reward_polling or not self._ad_reward_token:
            return
        self._ad_reward_polling = True

        def worker():
            result = get_ad_reward_status(self._ad_reward_token, timeout=15)
            result["event"] = "status"
            self.signals.ad_reward_result_signal.emit(result)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_ad_reward_polling(self):
        if self._ad_reward_timer is not None:
            self._ad_reward_timer.stop()
            self._ad_reward_timer.deleteLater()
            self._ad_reward_timer = None
        self._ad_reward_token = ""
        self._ad_reward_polling = False

    def _set_ad_reward_controls(self, enabled, busy_text=""):
        top_button = getattr(self, "btn_ad_reward_top", None)
        if top_button is not None:
            top_button.setEnabled(enabled)
            top_button.setText(busy_text or "免费领取额度")

        page = getattr(self, "page_about", None)
        page_button = getattr(page, "btn_ad_reward", None)
        if page_button is not None:
            page_button.setEnabled(enabled)
            page_button.setText(busy_text or "免费获取下载次数")

    def _handle_ad_reward_result(self, result):
        event = result.get("event")

        if event == "created":
            if not result.get("valid"):
                self._set_ad_reward_controls(True)
                status = result.get("status", "")
                if status == "ad_reward_daily_limit":
                    message = "今天可领取免费额度的次数已经用完。"
                elif status == "ad_reward_cooldown":
                    message = f"领取过于频繁，请等待 {result.get('cooldown_remaining', 0)} 秒后再试。"
                elif status == "expired":
                    message = "账号应已回归免费订阅，请刷新账号状态后重新领取。"
                elif status == "paid_subscription_not_eligible":
                    message = (
                        "有效付费订阅用户不能参加免费额度领取。"
                        "订阅到期并回归免费订阅后可再次参加。"
                    )
                elif status in ("ad_reward_unavailable", "ad_reward_not_configured"):
                    message = "免费领取功能暂未开放，请使用首次赠送额度或选择付费订阅套餐。"
                else:
                    raw_message = str(result.get("msg", "") or "")
                    if raw_message in ("ad_reward_not_configured", "ad_reward_unavailable"):
                        message = "免费领取功能暂未开放，请使用首次赠送额度或选择付费订阅套餐。"
                    else:
                        message = raw_message or "免费领取功能暂时不可用，请稍后重试。"
                QMessageBox.warning(self, "免费次数", message)
                return

            self._ad_reward_token = result.get("reward_token", "")
            reward_url = result.get("reward_url", "")
            try:
                webbrowser.open(reward_url)
            except Exception as exc:
                self._stop_ad_reward_polling()
                self._set_ad_reward_controls(True)
                QMessageBox.warning(self, "免费次数", f"无法打开官网领取页面：{exc}")
                return

            self._set_ad_reward_controls(False, "等待领取完成...")
            self._ad_reward_timer = QTimer(self)
            self._ad_reward_timer.timeout.connect(self._poll_ad_reward_status)
            self._ad_reward_timer.start(2500)
            self._poll_ad_reward_status()
            self.log_handler.log(
                f"已打开旧版免费额度页面，可领取 {result.get('reward_count', 3)} 次"
            )
            return

        self._ad_reward_polling = False
        if not result.get("valid"):
            if result.get("status") not in ("network_error",):
                self._stop_ad_reward_polling()
                self._set_ad_reward_controls(True)
            return

        reward_status = result.get("reward_status")
        if reward_status == "granted":
            self.account_info = result
            self.auth_status = "ok"
            self.auth_msg = "account_valid"
            self.authorized = True
            reward_count = int(result.get("reward_count", 0) or 0)
            remaining = result.get("today_download_remaining", 0)
            self._stop_ad_reward_polling()
            self._refresh_about_page()
            self.log_handler.log(
                f"免费次数领取成功：+{reward_count} 次，今日剩余 {remaining} 次"
            )
            QMessageBox.information(
                self,
                "领取成功",
                f"已获得 {reward_count} 次下载次数。\n今日剩余可下载 {remaining} 次。",
            )
        elif reward_status == "expired":
            self._stop_ad_reward_polling()
            self._set_ad_reward_controls(True)
            QMessageBox.warning(self, "免费次数", "领取链接已过期，请重新申请。")

    def open_download_folder(self):
        """Internal helper."""
        download_path = self.config.get("download_path", "")
        if not download_path or not os.path.exists(download_path):
            download_path = os.path.expanduser("~/Downloads")
        
        try:
            if os.path.exists(download_path):
                if platform.system().lower().startswith("win"):
                    os.startfile(download_path)
                else:
                    subprocess.Popen(['xdg-open', download_path])
                self.log_handler.log(f"已打开下载文件夹: {download_path}")
            else:
                self.log_handler.log(f"下载文件夹不存在: {download_path}")
        except Exception as e:
            self.log_handler.log(f"打开下载文件夹失败: {e}")

    def open_video_preview(self, file_path):
        """Open a downloaded video with the user's default local player."""
        try:
            if not file_path or not os.path.exists(file_path):
                QMessageBox.warning(self, "预览播放", "视频文件不存在，无法预览。")
                return

            if platform.system().lower().startswith("win"):
                os.startfile(file_path)
            elif platform.system().lower() == "darwin":
                subprocess.Popen(["open", file_path])
            else:
                subprocess.Popen(["xdg-open", file_path])
            self.log_handler.log(f"使用本地播放器打开预览: {file_path}")
        except Exception as e:
            self.log_handler.log(f"预览播放失败: {e}")
            QMessageBox.warning(self, "预览播放失败", str(e))
    
    def open_disclaimer(self):
        """Internal helper."""
        try:
            webbrowser.open(get_app_value("client.website.disclaimer", "https://www.muyanshidai.com/disclaimer"))
            if self.log_handler:
                self.log_handler.log("已打开免费声明页面")
        except Exception as e:
            if self.log_handler:
                self.log_handler.log(f"打开免费声明页面失败：{e}")
    
    def open_privacy_policy(self):
        """Internal helper."""
        try:
            webbrowser.open(get_app_value("client.website.privacy", "https://www.muyanshidai.com/privacy"))
            if self.log_handler:
                self.log_handler.log("已打开隐私政策页面")
        except Exception as e:
            if self.log_handler:
                self.log_handler.log(f"打开隐私政策页面失败：{e}")
    
    def open_terms_of_use(self):
        """Internal helper."""
        try:
            webbrowser.open(get_app_value("client.website.terms", "https://www.muyanshidai.com/terms"))
            if self.log_handler:
                self.log_handler.log("已打开使用条款页面")
        except Exception as e:
            if self.log_handler:
                self.log_handler.log(f"打开使用条款页面失败：{e}")
    
    def toggle_always_on_top(self):
        """Internal helper."""
        try:
            self.is_always_on_top = not self.is_always_on_top
            self.setWindowFlag(Qt.WindowStaysOnTopHint, self.is_always_on_top)
            self.show()
            if self.is_always_on_top:
                #
                self.btn_always_on_top.setText("📌")
                self.btn_always_on_top.setStyleSheet("""
                    QPushButton#btn_always_on_top {
                        background: transparent;
                        border: none;
                        color: #2E7892;
                        font-size: 15px;
                        min-width: 27px;
                        height: 27px;
                        padding: 0;
                        margin: 0;
                    }
                    QPushButton#btn_always_on_top:hover {
                        background: transparent;
                        color:#17445C;
                    }
                """)
                self.btn_always_on_top.setToolTip("取消窗口置顶")
                self.log_handler.log("窗口已设置为置顶")
            else:
                #
                self.btn_always_on_top.setText("📌")
                self.btn_always_on_top.setStyleSheet("""
                    QPushButton#btn_always_on_top {
                        background: transparent;
                        border: none;
                        color: #17445C;
                        font-size: 15px;
                        min-width: 27px;
                        height: 27px;
                        padding: 0;
                        margin: 0;
                    }
                    QPushButton#btn_always_on_top:hover {
                        background: transparent;
                        color:#2E7892;
                    }
                """)
                self.btn_always_on_top.setToolTip("切换窗口置顶状态")
                self.log_handler.log("窗口已取消置顶")
        except Exception as e:
            self.log_handler.log(f"切换窗口置顶状态失败: {e}")
            QMessageBox.warning(self, "错误", f"切换窗口置顶状态失败: {e}")
    

    
    def restart_application(self):
        """Internal helper."""
        try:
            reply = QMessageBox.question(self, "重启软件", "确定要重启软件吗？",
                                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.log_handler.log("正在重启软件...")
                subprocess.Popen(
                    get_restart_command(),
                    cwd=BASE_DIR,
                    close_fds=True,
                    creationflags=get_restart_creationflags(),
                )
                QTimer.singleShot(200, QApplication.quit)
        except Exception as e:
            self.log_handler.log(f"重启软件失败: {e}")
            QMessageBox.warning(self, "错误", f"重启软件失败: {e}")
    


    def _log(self, text):
        """Internal helper."""
        self.log_handler.log(text)

    def _update_task_stats(self):
        """Internal helper."""
        try:
            #
            if hasattr(self, 'lbl_total_tasks'):
                self.lbl_total_tasks.setText(f"总任务: {self.total_tasks}")
            if hasattr(self, 'lbl_completed_tasks'):
                self.lbl_completed_tasks.setText(f"完成: {self.completed_tasks}")
            if hasattr(self, 'lbl_failed_tasks'):
                self.lbl_failed_tasks.setText(f"失败: {self.failed_tasks}")
            
            #
            for page_name in (
                'page_video_download',
                'page_youtube_download',
                'page_tiktok_download',
                'page_instagram_download',
                'page_twitter_download',
            ):
                page = getattr(self, page_name, None)
                if page is not None:
                    page.update_task_stats(self.total_tasks, self.completed_tasks, self.failed_tasks)
        except Exception as e:
            self.log_handler.log(f"任务统计更新失败: {e}")

    def dragEnterEvent(self, event):
        """Internal helper."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Internal helper."""
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path.endswith('.txt'):
                if self.pages.currentIndex() == 1:
                    self.log_handler.log("视频下载页面不支持导入 TXT 文件，请使用主页提取功能")
            else:
                if self.pages.currentIndex() == 1 and self._ensure_page_loaded(1):
                    self.page_video_download.input_box.append(file_path)
                    self.log_handler.log(f"已添加文件: {file_path}")

    def on_language_changed(self, index):
        """Internal helper."""
        language = self.combo_language.itemData(index) if hasattr(self, "combo_language") else None
        if not language or language == self.language:
            return
        self.set_language(language)

    def set_language(self, language):
        """Internal helper."""
        if not language or language == self.language:
            return
        self.language = language
        self.config.set("language", self.language)
        self._refresh_texts_after_language()
        if self.log_handler:
            self.log_handler.log(f"语言已切换为: {'中文' if language == 'zh' else 'English'}")

    def prompt_for_account(self):
        """Internal helper."""
        auth_data = load_auth_data()
        if auth_data.get("token"):
            reply = QMessageBox.question(
                self,
                "退出确认",
                "当前账号已登录，是否退出？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply != QMessageBox.Yes:
                return False

            success, msg = logout_account_with_server()
            AuthCacheManager().force_refresh()
            clear_auth_data()
            self.authorized = False
            self.auth_mode = "account"
            self.auth_status = "no_account"
            self.auth_msg = msg or "已退出登录"
            self.expire_date = ""
            self.account_info = {}
            self._update_login_button_text()
            self._refresh_about_page()
            self.log_handler.log(msg or "已退出登录")
            try:
                self.statusBar().showMessage(msg or "已退出登录", 4000)
            except Exception:
                pass
            QMessageBox.information(self, "已退出", msg or "已退出登录")
            return success

        dialog = AccountAuthDialog(self)
        dialog_result = dialog.exec()
        result_data = dialog.result_data
        dialog.close()
        dialog.deleteLater()
        if dialog_result != QDialog.Accepted or not result_data:
            return False

        result = result_data
        data = result.get("data", {})
        save_account_session(data)
        AuthCacheManager().update_cache({
            "valid": True,
            "expire_date": data.get("expire_date", ""),
            "msg": data.get("msg", "登录成功"),
            "status": data.get("status", "ok"),
            "auth_mode": "account",
            "account": data,
        })
        self._apply_account_login_state(data)
        self.log_handler.log("账号登录成功")
        try:
            self.statusBar().showMessage(f"已登录：{data.get('email') or data.get('phone') or '账号用户'}", 4000)
        except Exception:
            pass
        return True



    def _format_file_size(self, size_bytes):
        """Internal helper."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"

    def _refresh_texts_after_language(self):
        """Internal helper."""
        try:
            self.setWindowTitle(WINDOW_TITLE)
            if hasattr(self.lbl_title, 'setText') and not hasattr(self.lbl_title, 'pixmap'):
                self.lbl_title.setText(get_app_value("client.app_tag", WINDOW_TITLE))
            if hasattr(self, 'combo_language'):
                self.combo_language.blockSignals(True)
                self.combo_language.setCurrentIndex(0 if self.language == "zh" else 1)
                self.combo_language.blockSignals(False)
            if hasattr(self, 'btn_language'):
                self.btn_language.setText("中文 ▾" if self.language == "zh" else "English ▾")
            
            if hasattr(self, 'btn_website'):
                self.btn_website.setText("订阅")
            
            if hasattr(self, 'btn_account_login'):
                self._update_login_button_text()
            
            self._update_sidebar_display()
            
            page_video_download = getattr(self, 'page_video_download', None)
            if page_video_download is not None:
                page_video_download.btn_paste.setText("粘贴链接")
                page_video_download.btn_start.setText("开始下载")
                page_video_download.btn_open_folder.setText("打开下载文件夹")
                page_video_download.input_box.setPlaceholderText("请输入视频链接，每行一个")
            
            self.statusBar().showMessage("状态：就绪")
            

            
            self._update_task_stats()
            
        except Exception as e:
            self.log_handler.log(f"界面文字刷新失败: {e}")

    def _start_video_download(self, urls, save_path, download_type):
        """Internal helper."""
        if not self.authorized:
            reply = QMessageBox.question(
                self, "",
                "请先登录账号后再下载。是否现在登录？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes and self.prompt_for_account():
                pass
            else:
                return False

        if not self.authorized:
            return False

        if not urls:
            self.log_handler.log("输入框为空，请输入链接。")
            return False

        if not save_path or not os.path.exists(save_path):
            save_path = QFileDialog.getExistingDirectory(self, "选择保存目录")
            if not save_path:
                self.log_handler.log("未选择保存目录，取消下载")
                return False

        if not os.path.exists(self.yt_dlp_path):
            self.log_handler.log("缺少 yt-dlp.exe")
            self.log_handler.log("下载地址: https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe")
            QMessageBox.warning(self, "缺少工具", "缺少 yt-dlp.exe，请更新插件或手动放到软件根目录。")
            return False

        if not os.path.exists(self.ffmpeg_path):
            self.log_handler.log("缺少 ffmpeg.exe")
            self.log_handler.log("下载地址: https://www.gyan.dev/ffmpeg/builds/")
            QMessageBox.warning(self, "缺少工具", "缺少 ffmpeg.exe，请更新插件或手动放到软件根目录。")
            return False

        reservation_token = self._consume_account_download_quota(
            len(urls), "主下载页面批量下载"
        )
        if not reservation_token:
            return False
        self.active_download_reservation_token = reservation_token

        self._reset_current_progress()
        last_preview_file = self.config.get("last_preview_file", "") or self.config.get("last_youtube_preview_file", "")
        if last_preview_file and os.path.exists(last_preview_file):
            self._set_latest_preview_file(last_preview_file)
        else:
            for page_name in (
                "page_video_download",
                "page_youtube_download",
                "page_tiktok_download",
                "page_instagram_download",
                "page_twitter_download",
                "page_batch_extract",
            ):
                page = getattr(self, page_name, None)
                if page is not None and hasattr(page, "clear_preview_file"):
                    page.clear_preview_file()
        
        self.download_type = "video"
        self.last_save_path = save_path
        
        #
        self.total_tasks = len(urls)
        self.completed_tasks = 0
        self.failed_tasks = 0
        self.task_progress = {u: 0.0 for u in urls}
        self.task_status = {u: "pending" for u in urls}
        self.is_downloading = True
        
        #
        self._update_task_stats()
        
        self.log_handler.log(f"开始视频下载 {len(urls)} 个任务")
        self.log_handler.log(f"下载路径: {save_path}")
        self.log_handler.log("画质: 最佳画质")
        from core.download_router import identify_platform

        platform_summary = {}
        for current_url in urls:
            platform_name = identify_platform(current_url)
            platform_summary[platform_name] = platform_summary.get(platform_name, 0) + 1
        if platform_summary:
            summary_text = " | ".join(f"{name}:{count}" for name, count in platform_summary.items())
            self.log_handler.log(f"下载调度: {summary_text}")
        
        #

        try:
            self.statusBar().showMessage("状态：下载中...")
        except Exception:
            pass

        with self.queue_lock:
            self.download_queue = [(u, save_path) for u in urls]
            self.active_downloads = {}

        #
        downloader_core = self._create_video_downloader_core(
            check_completion_callback=self._check_all_tasks_completed,
            update_task_stats_callback=self._update_task_stats,
            signals=self.signals,
            log_handler=self.log_handler,
        )
        self.active_downloader_core = downloader_core
        try:
            downloader_core.start_download(urls, save_path, "video")
        except Exception:
            self._settle_account_download_quota(
                reservation_token, False, settled_count=len(urls)
            )
            self.active_download_reservation_token = ""
            self.active_downloader_core = None
            self.is_downloading = False
            raise
        
        return True

    def _create_video_downloader_core(
        self,
        update_progress_callback=None,
        check_completion_callback=None,
        update_task_stats_callback=None,
        signals=None,
        log_handler=None,
    ):
        """Create one consistently configured download core for all entry points."""
        from videodown import VideoDownloaderCore

        return VideoDownloaderCore(
            yt_dlp_path=self.yt_dlp_path,
            ffmpeg_path=self.ffmpeg_path,
            deno_path=self.deno_path,
            config=self.config.config,
            signals=signals,
            log_handler=log_handler or self.log_handler,
            update_progress_callback=update_progress_callback,
            check_completion_callback=check_completion_callback,
            update_task_stats_callback=update_task_stats_callback,
            cookie_status=self.cookie_status,
            enable_deno=self.enable_deno,
            cookie_file=self.cookie_file,
            instagram_cookie_file=self.instagram_cookie_file,
            tiktok_cookie_file=self.tiktok_cookie_file,
            twitter_cookie_file=self.twitter_cookie_file,
        )

    def _check_all_tasks_completed(self):
        """Internal helper."""
        try:
            with self.queue_lock:
                completed_count = self.completed_tasks + self.failed_tasks
                queue_empty = len(self.download_queue) == 0
                no_active_downloads = len(self.active_downloads) == 0
                
                if completed_count >= self.total_tasks and queue_empty and no_active_downloads:
                    self.is_downloading = False
                    self.active_downloader_core = None
                    self.active_download_reservation_token = ""
                    
                    self.signals.status_signal.emit("下载完成")
                    
                    self.log_handler.log("全部视频下载任务完成！")
                    
                    self.statusBar().showMessage("状态：全部完成 ✅")
                    self.signals.speed_signal.emit("0 KB/s")
                    
        except Exception:
            pass

    def update_cookie_status(self):
        """Internal helper."""
        #
        self.refresh_cookie_health(log_results=False)

    def _cookie_paths_by_platform(self):
        return {
            "youtube": self.cookie_file,
            "instagram": self.instagram_cookie_file,
            "tiktok": self.tiktok_cookie_file,
            "twitter": self.twitter_cookie_file,
        }

    def refresh_cookie_health(self, log_results=False):
        previous_status = getattr(self, "cookie_status", {})
        self.cookie_status = CookieHealthManager.refresh_status_map(
            self._cookie_paths_by_platform(),
            previous_status,
        )

        if log_results:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_handler.log(f"[{timestamp}] Cookie health check:")
            for platform_key in ("youtube", "instagram", "tiktok", "twitter"):
                status = self.cookie_status[platform_key]
                size_text = self._format_file_size(status["size_bytes"]) if status.get("size_bytes") else "0 B"
                self.log_handler.log(
                    f"  {status['display_name']}: {status['health_label']} | "
                    f"score={status['health_score']} | size={size_text} | {status['reminder']}"
                )

        self.update_cookie_status_display()
        return self.cookie_status

    def _check_cookie_status_once(self):
        """Internal helper."""
        self.refresh_cookie_health(log_results=True)

    def update_cookie_status_display(self):
        """Internal helper."""
        status_text, all_ready, any_warning, any_reimport = CookieHealthManager.summarize_status(self.cookie_status)
        status_text_rich = CookieHealthManager.summarize_status_rich(self.cookie_status)
        if all_ready:
            label_text = f"✅ 所有 Cookie 健康 | {status_text_rich}"
            label_style = "font-size: 11px; margin-top: 2px; color: #10b981;"
        elif any_reimport:
            label_text = f"⚠️ 按平台检查 Cookie | {status_text_rich}"
            label_style = "font-size: 11px; margin-top: 2px; color: #f59e0b;"
        elif any_warning:
            label_text = f"⚠️ Cookie 需要关注 | {status_text_rich}"
            label_style = "font-size: 11px; margin-top: 2px; color: #f59e0b;"
        else:
            label_text = f"⚠️ 按平台检查 Cookie | {status_text_rich}"
            label_style = "font-size: 11px; margin-top: 2px; color: #f59e0b;"

        for page_name in [
            "page_video_download",
            "page_youtube_download",
            "page_tiktok_download",
            "page_instagram_download",
            "page_twitter_download",
            "page_batch_extract",
            "page_settings",
        ]:
            if not hasattr(self, page_name):
                continue
            page = getattr(self, page_name)
            if hasattr(page, "cookie_status_label_batch"):
                page.cookie_status_label_batch.setText(label_text)
                page.cookie_status_label_batch.setStyleSheet(label_style)
            elif hasattr(page, "cookie_status_label"):
                page.cookie_status_label.setText(label_text)
                page.cookie_status_label.setStyleSheet(label_style)

    def _apply_styles(self):
        """Internal helper."""
        app = QApplication.instance()
        app.setStyleSheet(self._dark_stylesheet())

    def _start_timers(self):
        """Internal helper."""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)
        self.timer.start(1000)
        
        self.speed_timer = QTimer(self)
        self.speed_timer.timeout.connect(self._update_speed_display)
        self.speed_timer.start(500)

    def closeEvent(self, event):
        core = getattr(self, "active_downloader_core", None)
        if core is not None:
            core.cancel_current_download()

        batch_page = getattr(self, "page_batch_extract", None)
        for thread in getattr(batch_page, "download_threads", []) if batch_page else []:
            thread_core = getattr(thread, "downloader_core", None)
            if thread_core is not None:
                thread_core.cancel_current_download()

        reservation_token = getattr(self, "active_download_reservation_token", "")
        remaining_count = max(
            0,
            int(getattr(self, "total_tasks", 0))
            - int(getattr(self, "completed_tasks", 0))
            - int(getattr(self, "failed_tasks", 0)),
        )
        if reservation_token and remaining_count:
            self._settle_account_download_quota(
                reservation_token,
                False,
                settled_count=remaining_count,
            )
            self.active_download_reservation_token = ""

        if batch_page:
            batch_token = getattr(batch_page, "batch_download_reservation_token", "")
            batch_remaining = max(
                0,
                int(getattr(batch_page, "_batch_download_total", 0))
                - int(getattr(batch_page, "_batch_download_completed", 0)),
            )
            if batch_token and batch_remaining:
                self._settle_account_download_quota(
                    batch_token,
                    False,
                    settled_count=batch_remaining,
                )
                batch_page.batch_download_reservation_token = ""

            for thread in getattr(batch_page, "download_threads", []):
                single_token = getattr(thread, "download_reservation_token", "")
                if single_token:
                    self._settle_account_download_quota(single_token, False)
                    thread.download_reservation_token = ""

        super().closeEvent(event)

    def _on_timer_tick(self):
        """Internal helper."""
        #
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%H:%M:%S")
        
        self.lbl_date.setText(f"{current_date}")
        self.lbl_time.setText(f"{current_time}")
        
        if self.is_downloading:
            try:
                completed = self.completed_tasks + self.failed_tasks
                total = self.total_tasks
                if total > 0:
                    status_text = f"已完成 {completed}/{total} | 成功: {self.completed_tasks} | 失败: {self.failed_tasks}"
                    if self.current_speed and self.current_speed != "0 KB/s":
                        status_text += f" | 閫熷害: {self.current_speed}"
                    self.statusBar().showMessage(status_text)
            except Exception:
                pass
    
    def _update_speed_display(self):
        """Internal helper."""
        if self.is_downloading and self.current_speed:
            try:
                completed = self.completed_tasks + self.failed_tasks
                total = self.total_tasks
                status_text = f"已完成 {completed}/{total}"
                status_text += f" | 速度: {self.current_speed}"
                self.statusBar().showMessage(status_text)
            except Exception:
                pass

    def _dark_stylesheet(self):
        """Internal helper."""
        return """
        QMainWindow { 
            background-color: #EEF4F7; 
            color: #0F172A; 
        }
        QFrame#topbar { 
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F8FBFC, stop:1 #EAF2F6); 
            border-bottom:1px solid #CFDDE5; 
        }
        QPushButton#topNavButton {
            background: transparent;
            border:none;
            color: #17445C;
            font-family: "Microsoft YaHei UI", "Segoe UI Semibold", "Segoe UI", Arial, sans-serif;
            font-size: 12px;
            font-weight: 700;
            min-width: 0px;
            padding: 0 7px;
            border-radius: 6px;
        }
        QPushButton#topNavButton:hover {
            background: rgba(23, 68, 92, 0.08);
            border:none;
            color: #2E7892;
        }
        QPushButton#topNavButton:pressed {
            background: rgba(23, 68, 92, 0.14);
            border:none;
        }
        QPushButton#topRewardButton {
            background: #0788AE;
            border: 1px solid #0788AE;
            border-radius: 8px;
            color: #FFFFFF;
            font-family: "Microsoft YaHei UI", "Segoe UI Semibold", "Segoe UI", Arial, sans-serif;
            font-size: 12px;
            font-weight: 700;
            padding: 0 10px;
        }
        QPushButton#topRewardButton:hover {
            background: #069CC5;
            border-color: #069CC5;
        }
        QPushButton#topRewardButton:pressed {
            background: #066C8A;
            border-color: #066C8A;
        }
        QPushButton#topRewardButton:disabled {
            background: #A8BBC4;
            border-color: #A8BBC4;
            color: #F8FBFC;
        }
        QFrame#sidebar { 
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F8FBFC, stop:1 #EAF2F6); 
            border-right:1px solid #CFDDE5; 
        }
        QLabel { 
            color: #0F172A; 
        }
        QPushButton { 
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FBFDFE, stop:1 #EEF4F7); 
            color:#17445C; 
            border:1px solid #CFDDE5; 
            padding:6px 8px; 
            border-radius:7px; 
            font-size: 11px;
            min-width: 70px;
        }
        QPushButton:hover { 
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2A6A82, stop:1 #17445C); 
            border:1px solid #6AB6CC;
            color:#FFFFFF;
        }
        QPushButton:pressed { 
            background-color:#173E52; 
            border:1px solid #2F6B84;
        }
        QPushButton:focus {
            outline: none;
        }
        QToolButton:focus {
            outline: none;
        }
        QPushButton:disabled { 
            background-color:#E2E8F0; 
            color:#94A3B8; 
            border:1px solid #CFDDE5; 
        }
        QPushButton#btn_always_on_top { 
            background: transparent; 
            border: none; 
            color: #17445C; 
            font-size: 15px; 
            min-width: 27px; 
            height: 27px; 
            padding: 0; 
            margin: 0; 
        }
        QPushButton#btn_always_on_top:hover { 
            background: transparent; 
            color: #2E7892;
        }
        QComboBox { 
            background-color:#FBFDFE; 
            color:#0F172A; 
            border:1px solid #C9D8E1; 
            border-radius:6px;
            padding:5px 7px; 
        }
        QComboBox:hover {
            border:1px solid #6A9CAF;
        }
        QTextEdit { 
            background-color:#FBFDFE; 
            color:#0F172A; 
            border:1px solid #C9D8E1; 
            border-radius:7px; 
            padding:6px;
            selection-background-color:#24576D;
            selection-color:#FFFFFF;
        }
        QTextEdit:focus {
            border:1px solid #6A9CAF;
            background-color:#FFFFFF;
        }
        QTextEdit[readOnly="true"] {
            background-color:#FBFDFE;
            color:#111827;
        }
        QScrollBar:vertical {
            background:#EEF2F7;
            width:10px;
            margin:0;
        }
        QScrollBar::handle:vertical {
            background:#CFDDE5;
            border-radius:5px;
            min-height:26px;
        }
        QScrollBar::handle:vertical:hover {
            background:#94A3B8;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height:0;
        }
        QScrollBar:horizontal {
            background:#EEF2F7;
            height:10px;
            margin:0;
        }
        QScrollBar::handle:horizontal {
            background:#CFDDE5;
            border-radius:5px;
            min-width:26px;
        }
        QScrollBar::handle:horizontal:hover {
            background:#94A3B8;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width:0;
        }
        QProgressBar { 
            background-color:#E2E8F0; 
            border:1px solid #CFDDE5; 
            border-radius:6px; 
            height:12px; 
            text-align:center;
            color:#0F172A;
        }
        QProgressBar::chunk { 
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #24576D, stop:1 #2E7892); 
            border-radius:5px;
        }
        QPushButton#navButton {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #FBFDFE, stop:1 #EEF4F7);
            border:1px solid #CFDDE5;
            color: #17445C;
            font-family: "Microsoft YaHei UI", "Segoe UI", Arial, sans-serif;
            font-size: 12px;
            font-weight: 500;
            min-width: 0px;
            padding: 0 10px;
            text-align: left;
            border-radius: 7px;
        }
        QPushButton#navButton:hover {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2A6A82, stop:1 #17445C);
            border:1px solid #6AB6CC;
            color: #FFFFFF;
        }
        QPushButton#navButton:checked {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2A6A82, stop:1 #17445C);
            border:1px solid #6AB6CC;
            color: #FFFFFF;
        }
        QPushButton#navButton[collapsed="true"] {
            padding: 0;
            text-align: center;
        }
        QStatusBar { 
            background-color:#F8FBFC; 
            color:#475569; 
            border-top:1px solid #CFDDE5;
        }
        QFrame#downloadSummary {
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #F8FBFC, stop:1 #EAF2F6);
            border:1px solid #CFDDE5;
            border-radius:8px;
            padding:6px;
        }
        QFrame#settingGroup { 
            background-color:#F8FBFC;
            border:1px solid #CFDDE5; 
            border-radius:8px; 
            padding:8px; 
            margin-bottom:8px; 
        }
        QLineEdit { 
            background-color:#FBFDFE; 
            color:#0F172A; 
            border:1px solid #C9D8E1; 
            border-radius:6px; 
            padding:5px 7px; 
            selection-background-color:#24576D;
        }
        QLineEdit:focus {
            border:1px solid #6A9CAF;
            background-color:#FFFFFF;
        }
        QCheckBox {
            color: #0F172A;
            spacing:6px;
        }
        QCheckBox::indicator {
            width:13px;
            height:13px;
            border-radius:3px;
            border:1px solid #94A3B8;
            background:#FFFFFF;
        }
        QCheckBox::indicator:checked {
            background:#111827;
            border:1px solid #111827;
        }
        QTableWidget {
            background-color: #FBFDFE;
            color: #0F172A;
            gridline-color: #E2E8F0;
            border: 1px solid #C9D8E1;
            border-radius: 8px;
        }
        QTableWidget::item {
            padding: 4px;
            border-bottom: 1px solid #E2E8F0;
        }
        QTableWidget::item:selected {
            background-color: #1E293B;
            color: #FFFFFF;
        }
        QHeaderView::section {
            background-color: #EAF2F6;
            color: #0F172A;
            padding: 6px;
            border: 1px solid #CFDDE5;
        }
        QDialog {
            background-color: #F8FAFC;
            color: #0F172A;
        }
        QDialog QLabel {
            color: #0F172A;
        }
        QDialog QLineEdit, QDialog QTextEdit, QDialog QTextBrowser {
            background-color: #FBFDFE;
            color: #0F172A;
            border: 1px solid #C9D8E1;
            border-radius: 6px;
            padding: 6px;
        }
        QDialog QPushButton {
            background-color: #FBFDFE;
            color: #17445C;
            border: 1px solid #CFDDE5;
            padding: 6px 12px;
            border-radius: 6px;
        }
        QDialog QPushButton:hover {
            background-color: #17445C;
            color: #FFFFFF;
            border: 1px solid #6AB6CC;
        }
        QGroupBox {
            color: #0F172A;
            border: 1px solid #CFDDE5;
            border-radius: 8px;
            margin-top: 12px;
            padding-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        QMessageBox {
            background-color: #F8FAFC;
            color: #0F172A;
        }
        QMessageBox QLabel {
            color: #0F172A;
        }
        QMessageBox QPushButton {
            background-color: #FBFDFE;
            color: #17445C;
            border: 1px solid #CFDDE5;
            padding: 6px 12px;
            border-radius: 6px;
            min-width: 80px;
        }
        QMessageBox QPushButton:hover {
            background-color: #17445C;
            color: #FFFFFF;
            border: 1px solid #6AB6CC;
        }
        """



def run_package_self_test():
    """Verify the packaged runtime without opening the desktop UI."""
    report_path = os.path.join(USER_DATA_DIR, "package_self_test.json")
    try:
        arg_index = sys.argv.index(PACKAGE_SELF_TEST_ARG)
        if len(sys.argv) > arg_index + 1:
            report_path = os.path.abspath(sys.argv[arg_index + 1])
    except (ValueError, IndexError):
        pass

    required_files = (
        "Vidoon2026.exe",
        YT_DLP_NAME,
        FFMPEG_NAME,
        DENO_NAME,
        "config.json",
        "app_settings.json",
        "version.json",
        "icon.ico",
        "logo.png",
    )
    missing_files = [
        file_name
        for file_name in required_files
        if not os.path.isfile(os.path.join(BASE_DIR, file_name))
    ]

    storage_ok = False
    storage_error = ""
    storage_probe = os.path.join(USER_DATA_DIR, "package_write_test.tmp")
    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        with open(storage_probe, "w", encoding="utf-8") as file:
            file.write("ok")
        os.remove(storage_probe)
        storage_ok = True
    except Exception as exc:
        storage_error = str(exc)

    certificate_ok = False
    certificate_path = ""
    certificate_error = ""
    try:
        import certifi

        certificate_path = certifi.where()
        certificate_ok = os.path.isfile(certificate_path)
    except Exception as exc:
        certificate_error = str(exc)

    api_ok, api_data = get_public_site_config(timeout=20)
    report = {
        "ok": not missing_files and storage_ok and certificate_ok and api_ok,
        "frozen": bool(getattr(sys, "frozen", False)),
        "base_dir": BASE_DIR,
        "user_data_dir": USER_DATA_DIR,
        "missing_files": missing_files,
        "storage_ok": storage_ok,
        "storage_error": storage_error,
        "certificate_ok": certificate_ok,
        "certificate_path": certificate_path,
        "certificate_error": certificate_error,
        "api_ok": api_ok,
        "api_status": api_data.get("status", "") if isinstance(api_data, dict) else "",
        "api_message": api_data.get("msg", "") if isinstance(api_data, dict) else "",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    try:
        report_dir = os.path.dirname(report_path)
        if report_dir:
            os.makedirs(report_dir, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2)
    except Exception:
        return 3
    return 0 if report["ok"] else 2


def main():
    """Internal helper."""
    multiprocessing.freeze_support()
    if PACKAGE_SELF_TEST_ARG in sys.argv:
        raise SystemExit(run_package_self_test())

    app = QApplication(sys.argv)
    if not acquire_single_instance_lock():
        QMessageBox.information(None, "软件已在运行", "Vidoon 已经打开，请不要重复启动。")
        return

    try:
        if os.path.exists(ICON_FILE):
            app.setWindowIcon(QIcon(ICON_FILE))
        else:
            jpg_icon = os.path.join(BASE_DIR, "icon.jpg")
            if os.path.exists(jpg_icon):
                app.setWindowIcon(QIcon(jpg_icon))
    except Exception:
        pass
    
    window = VideoDownloader()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()




