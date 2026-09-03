"""
关于页面模块
"""

import os
import platform
import sys
import webbrowser
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app_config import get_app_value, get_app_version
from shouquan import load_auth_data
from ui_components import create_button, create_label


class AboutPage(QWidget):
    def __init__(self, parent=None, config=None, log_handler=None):
        super().__init__(parent)
        self.parent = parent
        self.config = config
        self.log_handler = log_handler

        self.BASE_DIR = self._resolve_base_dir()
        self.yt_dlp_path = os.path.join(self.BASE_DIR, "yt-dlp.exe")
        self.ffmpeg_path = os.path.join(self.BASE_DIR, "ffmpeg.exe")
        self.deno_path = os.path.join(self.BASE_DIR, "deno.exe")
        self.cookie_file = os.path.join(self.BASE_DIR, "cookies.txt")

        self.authorized = False
        self.expire_date = ""
        self.auth_status = ""
        self.auth_msg = ""

        self.init_ui()
        self.start_timer()

    def _resolve_base_dir(self):
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.abspath(os.path.dirname(__file__))

    def _section_title(self, text):
        return create_label(text, "font-weight: 700; font-size: 13px;")

    def _create_info_row(self, label_text, value_style=None, value_selectable=False):
        row = QHBoxLayout()
        row.setSpacing(8)

        label = create_label(label_text, "min-width: 96px;")
        value = create_label("-", value_style)
        if value_selectable:
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)

        row.addWidget(label)
        row.addWidget(value, 1)
        row.addStretch()
        return row, value

    def _add_separator(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        layout.addWidget(line)

    def _format_download_limit_text(self, account):
        if not account:
            return "-"

        max_devices = account.get("max_devices", "-")
        if self._is_account_subscription_expired(account):
            return f"{max_devices} 台 / 已停用"

        daily_limit = account.get(
            "effective_daily_download_limit",
            account.get("free_daily_limit", "-1"),
        )
        per_task_limit = account.get("per_task_limit", 1)
        today_count = account.get("today_download_count", 0)
        remaining = account.get("today_download_remaining", -1)
        quota_mode = str(account.get("quota_mode", "daily") or "daily")

        try:
            max_devices = int(max_devices)
        except Exception:
            max_devices = "-"

        try:
            daily_limit = int(daily_limit)
        except Exception:
            daily_limit = -1

        try:
            per_task_limit = int(per_task_limit)
        except Exception:
            per_task_limit = 1

        try:
            today_count = int(today_count)
        except Exception:
            today_count = 0

        try:
            remaining = int(remaining)
        except Exception:
            remaining = -1

        reward_count = int(account.get("today_ad_reward_count", 0) or 0)
        reward_text = f" / 今日免费领取 +{reward_count}" if reward_count > 0 else ""
        if quota_mode == "credit":
            remaining_text = max(0, remaining)
            return (
                f"{max_devices} 台 / 单次 {per_task_limit} 个 / "
                f"免费额度剩余 {remaining_text} 次{reward_text}"
            )

        if daily_limit < 0:
            download_text = "不限"
        else:
            download_text = f"{today_count}/{daily_limit}"
            if remaining >= 0:
                download_text += f"（剩余 {remaining}）"

        reward_text = f" / 免费领取 +{reward_count}" if reward_count > 0 else ""
        return f"{max_devices} 台 / 单次 {per_task_limit} 个 / 今日 {download_text}{reward_text}"

    def _is_account_subscription_expired(self, account):
        if not account:
            return False

        status = str(self.auth_status or account.get("status", "") or "").strip()
        msg = str(self.auth_msg or account.get("msg", "") or "").strip()
        if status == "expired" or msg == "subscription_expired":
            return True

        expire_date = account.get("expire_date") or account.get("expire_at") or self.expire_date
        if not expire_date:
            return False

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(expire_date), fmt) < datetime.now()
            except Exception:
                continue
        return False

    def _format_account_status(self, account):
        if not account:
            return "未登录", "font-weight: 500; color: #6b7280;"

        if self.authorized:
            return "已登录", "font-weight: 500; color: #10b981;"
        if self._is_account_subscription_expired(account):
            return "订阅已过期", "font-weight: 500; color: #ef4444;"
        return "已登录，未开通订阅", "font-weight: 500; color: #f59e0b;"

    def _format_account_level_text(self, account):
        if not account:
            return "未登录"
        if self._is_account_subscription_expired(account):
            return "订阅已过期"
        return account.get("account_level_label") or "免费订阅"

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        account_layout = QVBoxLayout()
        account_layout.setSpacing(8)
        account_layout.addWidget(self._section_title("账号信息"))

        account_columns = QHBoxLayout()
        account_columns.setSpacing(36)

        account_left_layout = QVBoxLayout()
        account_left_layout.setSpacing(8)

        row, self.lbl_account_about = self._create_info_row("登录账号:", value_selectable=True)
        account_left_layout.addLayout(row)

        row, self.lbl_account_status_about = self._create_info_row("账号状态:", "font-weight: 500;")
        account_left_layout.addLayout(row)

        account_right_layout = QVBoxLayout()
        account_right_layout.setSpacing(8)

        row, self.lbl_account_level_about = self._create_info_row("用户级别:")
        account_right_layout.addLayout(row)

        row, self.lbl_account_expire_about = self._create_info_row("到期时间:")
        account_right_layout.addLayout(row)

        row, self.lbl_account_device_about = self._create_info_row("设备/次数:")
        account_right_layout.addLayout(row)

        account_columns.addLayout(account_left_layout, 1)
        account_columns.addLayout(account_right_layout, 1)
        account_layout.addLayout(account_columns)

        reward_row = QHBoxLayout()
        reward_row.addStretch()
        self.btn_ad_reward = create_button(
            "免费获取下载次数",
            30,
            132,
            "前往官网免费领取下载次数",
        )
        self.btn_ad_reward.clicked.connect(self.open_ad_reward)
        self.btn_ad_reward.setVisible(False)
        reward_row.addWidget(self.btn_ad_reward)
        account_layout.addLayout(reward_row)

        layout.addLayout(account_layout)
        self._add_separator(layout)

        sys_info_layout = QVBoxLayout()
        sys_info_layout.setSpacing(6)
        sys_title_row = QHBoxLayout()
        sys_title_row.setSpacing(8)
        sys_title_row.addWidget(self._section_title("系统信息"))
        self.btn_check_update = create_button("检查更新", 28, 86, "检查是否有新版本")
        self.btn_check_update.clicked.connect(self.check_for_updates)
        sys_title_row.addWidget(self.btn_check_update)
        sys_title_row.addStretch()
        sys_info_layout.addLayout(sys_title_row)
        self.sys_info_text = create_label(
            "",
            "font-size: 11px; background-color: #FBFDFE; color: #0F172A; border: 1px solid #C9D8E1; padding: 8px; border-radius: 8px;"
        )
        self.sys_info_text.setWordWrap(True)
        self.sys_info_text.setTextFormat(Qt.RichText)
        sys_info_layout.addWidget(self.sys_info_text)
        layout.addLayout(sys_info_layout)
        self._add_separator(layout)

        contact_layout = QVBoxLayout()
        contact_layout.setSpacing(6)
        contact_layout.addWidget(self._section_title("联系我们"))

        mail_layout = QHBoxLayout()
        mail_layout.setSpacing(6)
        mail_layout.addWidget(create_label(f"邮箱: {get_app_value('client.support_email', '842635534@qq.com')}"))
        mail_layout.addStretch()
        contact_layout.addLayout(mail_layout)

        contact_layout.addWidget(create_label("售后微信：w842635534 【订阅请直接加微信】"))
        layout.addLayout(contact_layout)

        layout.addStretch()
        self.refresh_info()

    def _build_tools_text(self):
        yt_dlp_exists = os.path.exists(self.yt_dlp_path)
        ffmpeg_exists = os.path.exists(self.ffmpeg_path)
        deno_exists = os.path.exists(self.deno_path)
        cookie_exists = os.path.exists(self.cookie_file)

        text = f"yt-dlp: {'已安装' if yt_dlp_exists else '未安装'}<br>"
        text += f"FFmpeg: {'已安装' if ffmpeg_exists else '未安装'}<br>"
        text += f"视频解析运行环境: {'已就绪' if deno_exists else '未检测到'}<br>"
        text += f"Cookie 文件: {'已配置' if cookie_exists else '未配置'}"
        return text

    def check_for_updates(self):
        if self.parent and hasattr(self.parent, "check_for_updates"):
            self.parent.check_for_updates(silent=False)
        else:
            QMessageBox.information(self, "检查更新", "当前窗口未连接更新服务。")

    def set_config(self, config):
        self.config = config

    def set_log_handler(self, log_handler):
        self.log_handler = log_handler

    def update_auth_info(self, auth_info):
        if not auth_info:
            return

        self.authorized = auth_info.get("valid", False)
        self.expire_date = auth_info.get("expire_date", "")
        self.auth_status = auth_info.get("status", "")
        self.auth_msg = auth_info.get("msg", "")

        account = auth_info.get("account", {}) or {}
        auth_data = load_auth_data()

        if account or auth_data.get("token"):
            current = account or auth_data
            self.lbl_account_about.setText(current.get("email") or current.get("phone") or "-")
            status_text, status_style = self._format_account_status(current)
            self.lbl_account_status_about.setText(status_text)
            self.lbl_account_status_about.setStyleSheet(status_style)
            self.lbl_account_level_about.setText(self._format_account_level_text(current))
            self.lbl_account_expire_about.setText(current.get("expire_date") or current.get("expire_at") or "-")
            self.lbl_account_device_about.setText(self._format_download_limit_text(current))
            reward_enabled = bool(auth_info.get("ad_reward_enabled", False))
            account_level = str(current.get("account_level", "free")).strip().lower()
            reward_eligible = account_level in ("free", "trial")
            self.btn_ad_reward.setVisible(
                bool(self.authorized and reward_enabled and reward_eligible)
            )
            self.btn_ad_reward.setEnabled(True)
            self.btn_ad_reward.setText("免费获取下载次数")
        else:
            self.lbl_account_about.setText("-")
            self.lbl_account_status_about.setText("未登录")
            self.lbl_account_status_about.setStyleSheet("font-weight: 500; color: #6b7280;")
            self.lbl_account_level_about.setText("未登录")
            self.lbl_account_expire_about.setText("-")
            self.lbl_account_device_about.setText("-")
            self.btn_ad_reward.setVisible(
                bool(auth_info.get("ad_reward_enabled", False))
            )

    def open_ad_reward(self):
        if self.parent and hasattr(self.parent, "open_ad_reward"):
            self.parent.open_ad_reward()
            return
        QMessageBox.warning(self, "免费次数", "当前窗口未连接免费额度领取服务。")

    def update_tools_status(self):
        if hasattr(self, "tools_status"):
            self.tools_status.setText(self._build_tools_text())

    def open_disclaimer(self):
        try:
            webbrowser.open(get_app_value("client.website.disclaimer", "https://www.muyanshidai.com/disclaimer"))
            if self.log_handler:
                self.log_handler.log("已打开免责声明页面")
        except Exception as exc:
            if self.log_handler:
                self.log_handler.log(f"打开免责声明页面失败: {exc}")

    def open_privacy_policy(self):
        try:
            webbrowser.open(get_app_value("client.website.privacy", "https://www.muyanshidai.com/privacy"))
            if self.log_handler:
                self.log_handler.log("已打开隐私政策页面")
        except Exception as exc:
            if self.log_handler:
                self.log_handler.log(f"打开隐私政策页面失败: {exc}")

    def open_terms_of_use(self):
        try:
            webbrowser.open(get_app_value("client.website.terms", "https://www.muyanshidai.com/terms"))
            if self.log_handler:
                self.log_handler.log("已打开使用条款页面")
        except Exception as exc:
            if self.log_handler:
                self.log_handler.log(f"打开使用条款页面失败: {exc}")

    def refresh_info(self):
        self.BASE_DIR = self._resolve_base_dir()
        self.yt_dlp_path = os.path.join(self.BASE_DIR, "yt-dlp.exe")
        self.ffmpeg_path = os.path.join(self.BASE_DIR, "ffmpeg.exe")
        self.deno_path = os.path.join(self.BASE_DIR, "deno.exe")
        self.cookie_file = os.path.join(self.BASE_DIR, "cookies.txt")

        if hasattr(self, "sys_info_text"):
            self.sys_info_text.setText(
                f"软件名称: {get_app_value('client.app_display_name', 'Vidoon 视频素材管理工具 2026')}<br>"
                f"版本: {get_app_version()}<br>"
                f"{get_app_value('client.copyright_text', '© 2026 Vidoon 版权所有')}<br>"
                f"操作系统: {platform.system()} {platform.release()}<br>"
                f"Python 版本: {platform.python_version()}<br>"
                f"程序目录: {self.BASE_DIR}<br>"
                f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        self.update_tools_status()

    def start_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_info)
        self.timer.start(1000)

    def refresh_all(self):
        self.refresh_info()
        self.update_tools_status()
