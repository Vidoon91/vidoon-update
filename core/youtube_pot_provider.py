import atexit
import json
import os
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen


PROVIDER_VERSION = "1.3.2"
PROVIDER_PORT = 4416
PROVIDER_URL = f"http://127.0.0.1:{PROVIDER_PORT}"


class YouTubePotProviderManager:
    def __init__(self, app_dir: str):
        self.app_dir = os.path.abspath(app_dir)
        self.provider_dir = os.path.join(self.app_dir, "vendor", "bgutil-provider")
        self.node_path = os.path.join(self.provider_dir, "node.exe")
        self.server_dir = os.path.join(self.provider_dir, "server")
        self.server_script = os.path.join(self.server_dir, "build", "main.js")
        self.plugin_path = os.path.join(
            self.app_dir,
            "yt-dlp-plugins",
            "bgutil-ytdlp-pot-provider.zip",
        )
        self.base_url = PROVIDER_URL
        self._process = None
        self._lock = threading.Lock()

    def is_installed(self) -> bool:
        return all(
            os.path.isfile(path)
            for path in (self.node_path, self.server_script, self.plugin_path)
        )

    def _ping(self, timeout: float = 1.5) -> bool:
        try:
            with urlopen(f"{self.base_url}/ping", timeout=timeout) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
                return str(payload.get("version", "")) == PROVIDER_VERSION
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return False

    def ensure_ready(self, log_callback=None, timeout: float = 20.0) -> tuple[bool, str]:
        if self._ping():
            return True, "ready"
        if not self.is_installed():
            return False, "provider_not_installed"

        with self._lock:
            if self._ping():
                return True, "ready"
            if self._process and self._process.poll() is None:
                return self._wait_until_ready(timeout)

            if log_callback:
                log_callback("正在启动 YouTube 本地验证通道，首次启动可能需要几秒...")

            creationflags = 0
            startupinfo = None
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            try:
                self._process = subprocess.Popen(
                    [self.node_path, self.server_script, "--port", str(PROVIDER_PORT)],
                    cwd=self.server_dir,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
            except OSError:
                self._process = None
                return False, "provider_start_failed"

            return self._wait_until_ready(timeout)

    def _wait_until_ready(self, timeout: float) -> tuple[bool, str]:
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            if self._process and self._process.poll() is not None:
                return False, "provider_exited"
            if self._ping(timeout=0.8):
                return True, "ready"
            time.sleep(0.25)
        return False, "provider_timeout"

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


_MANAGERS: dict[str, YouTubePotProviderManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_youtube_pot_provider(app_dir: str) -> YouTubePotProviderManager:
    normalized_dir = os.path.normcase(os.path.abspath(app_dir))
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(normalized_dir)
        if manager is None:
            manager = YouTubePotProviderManager(normalized_dir)
            _MANAGERS[normalized_dir] = manager
        return manager


def _stop_all_managers() -> None:
    for manager in list(_MANAGERS.values()):
        manager.stop()


atexit.register(_stop_all_managers)
