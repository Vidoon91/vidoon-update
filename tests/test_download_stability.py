import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import shouquan
from about import AboutPage
from main import VideoDownloader
from core.download_types import DownloadRuntime, build_result
from core.download_utils import (
    detect_tool_update_notices,
    extract_output_file_paths,
    extract_output_ids,
    get_quality_label,
    get_success_message,
    maybe_log_download_progress,
    run_command,
    run_logged_download_strategy,
    validate_output_files,
)
from platforms.instagram_download import InstagramDownloader
from platforms.tiktok_download import TikTokDownloader
from platforms.twitter_download import TwitterDownloader
from platforms.youtube_download import YouTubeDownloader
from piliang import BatchExtractPage
from setting import DenoResolver
from videodown import VideoDownloadPage


class _LabelRecorder:
    def __init__(self):
        self.text = ""
        self.history = []

    def setText(self, text):
        self.text = text
        self.history.append(text)


class _BatchTableRecorder:
    def __init__(self, row_count=10):
        self.row_count = row_count
        self.scrolled_item = None
        self.preview_updates = []

    def rowCount(self):
        return self.row_count

    def item(self, row, column):
        return (row, column)

    def scrollToItem(self, item):
        self.scrolled_item = item

    def setCellWidget(self, row, column, widget):
        self.preview_updates.append((row, column, widget))


class _ButtonTextRecorder:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _BatchScrollHarness:
    _visible_table_row_for_url = BatchExtractPage._visible_table_row_for_url
    _scroll_to_download_row = BatchExtractPage._scroll_to_download_row
    _refresh_row_preview = BatchExtractPage._refresh_row_preview
    _is_batch_selectable = staticmethod(BatchExtractPage._is_batch_selectable)
    toggle_batch_selection = BatchExtractPage.toggle_batch_selection


class BatchDownloadScrollTests(unittest.TestCase):
    def test_next_batch_item_is_scrolled_into_view_without_rebuilding_table(self):
        page = _BatchScrollHarness()
        page.extracted_data = [{"url": f"url-{index}"} for index in range(10)]
        page.current_page = 1
        page.items_per_page = 10
        page.table_results = _BatchTableRecorder()

        page._scroll_to_download_row("url-7")

        self.assertEqual(page.table_results.scrolled_item, (7, 1))

    def test_preview_refresh_updates_only_the_completed_row(self):
        page = _BatchScrollHarness()
        page.extracted_data = [
            {"url": "url-1", "file_path": ""},
            {"url": "url-2", "file_path": "video.mp4"},
        ]
        page.current_page = 1
        page.items_per_page = 10
        page.table_results = _BatchTableRecorder(row_count=2)
        page._create_row_preview_button = lambda path: f"preview:{path}"

        page._refresh_row_preview("url-2")

        self.assertEqual(
            page.table_results.preview_updates,
            [(1, 6, "preview:video.mp4")],
        )

    def test_batch_selection_ignores_completed_rows_on_current_page(self):
        page = _BatchScrollHarness()
        page.batch_download_active = False
        page.current_page = 1
        page.items_per_page = 10
        page.main_window = None
        page.btn_select_batch = _ButtonTextRecorder()
        page.display_current_page = lambda: None
        page.extracted_data = [
            {"url": "done", "status": "完成", "selected": False},
            {"url": "downloaded", "status": "已下载", "selected": False},
            {"url": "active", "status": "下载中", "selected": False},
            {"url": "pending", "status": "未下载", "selected": False},
            {"url": "failed", "status": "失败", "selected": False},
        ]

        page.toggle_batch_selection()

        selected_urls = [
            item["url"] for item in page.extracted_data if item.get("selected")
        ]
        self.assertEqual(selected_urls, ["pending", "failed"])


class AccountLoginPersistenceTests(unittest.TestCase):
    @patch("shouquan.save_account_session", return_value=False)
    @patch(
        "shouquan._post_api",
        return_value={"status": "ok", "token": "session-1", "msg": "login_success"},
    )
    @patch("shouquan.get_machine_code", return_value="machine-1")
    def test_login_fails_when_local_session_cannot_be_saved(
        self,
        _get_machine_code,
        _post_api,
        _save_account_session,
    ):
        success, message, _data = shouquan.login_account_with_server("user@example.com", "secret")

        self.assertFalse(success)
        self.assertIn("无法保存登录状态", message)

    @patch("shouquan.save_account_session", return_value=True)
    @patch(
        "shouquan._post_api",
        return_value={"status": "ok", "token": "session-1", "msg": "login_success"},
    )
    @patch("shouquan.get_machine_code", return_value="machine-1")
    def test_login_succeeds_after_local_session_is_saved(
        self,
        _get_machine_code,
        _post_api,
        _save_account_session,
    ):
        success, _message, data = shouquan.login_account_with_server("user@example.com", "secret")

        self.assertTrue(success)
        self.assertEqual(data["token"], "session-1")


class DownloadQuotaSettlementTests(unittest.TestCase):
    @patch("shouquan.save_account_session")
    @patch("shouquan._post_api")
    @patch("shouquan.get_machine_code", return_value="machine-1")
    @patch("shouquan.get_saved_account_token", return_value="session-1")
    def test_reserve_returns_server_reservation_without_consuming_count(
        self,
        _saved_token,
        _machine_code,
        post_api,
        _save_session,
    ):
        post_api.return_value = {
            "status": "ok",
            "valid": True,
            "reservation_token": "reservation-1",
            "today_download_count": 4,
        }

        result = shouquan.reserve_download_permission(3)

        self.assertTrue(result["valid"])
        self.assertEqual(result["reservation_token"], "reservation-1")
        payload = post_api.call_args.args[0]
        self.assertEqual(payload["action"], "reserve_download")
        self.assertEqual(payload["url_count"], 3)

    @patch("shouquan.save_account_session")
    @patch("shouquan._post_api")
    @patch("shouquan.get_machine_code", return_value="machine-1")
    @patch("shouquan.get_saved_account_token", return_value="session-1")
    def test_failed_download_settles_with_zero_success_count(
        self,
        _saved_token,
        _machine_code,
        post_api,
        _save_session,
    ):
        post_api.return_value = {"status": "ok", "valid": True}

        result = shouquan.settle_download_permission(
            "reservation-1",
            False,
        )

        self.assertTrue(result["valid"])
        payload = post_api.call_args.args[0]
        self.assertEqual(payload["action"], "settle_download")
        self.assertEqual(payload["settled_count"], 1)
        self.assertEqual(payload["success_count"], 0)

    @patch("shouquan.save_account_session")
    @patch("shouquan._post_api")
    @patch("shouquan.get_machine_code", return_value="machine-1")
    @patch("shouquan.get_saved_account_token", return_value="session-1")
    def test_successful_download_settles_with_one_success(
        self,
        _saved_token,
        _machine_code,
        post_api,
        _save_session,
    ):
        post_api.return_value = {"status": "ok", "valid": True}

        result = shouquan.settle_download_permission(
            "reservation-1",
            True,
        )

        self.assertTrue(result["valid"])
        payload = post_api.call_args.args[0]
        self.assertEqual(payload["settled_count"], 1)
        self.assertEqual(payload["success_count"], 1)


class AdRewardClientTests(unittest.TestCase):
    @patch("main.webbrowser.open")
    def test_client_reward_button_opens_homepage_entry(self, browser_open):
        page = SimpleNamespace(
            _ad_reward_enabled=False,
            _is_ad_reward_account_eligible=lambda: self.fail(
                "client account state must not be checked before opening the homepage"
            ),
            log_handler=SimpleNamespace(log=lambda _message: None),
        )

        VideoDownloader.open_ad_reward(page)

        browser_open.assert_called_once_with(
            "https://license.muyanshidai.com/index.php"
        )

    def test_reward_eligibility_is_limited_to_free_accounts(self):
        free_page = SimpleNamespace(
            authorized=True,
            account_info={"account_level": "free"},
        )
        self.assertTrue(
            VideoDownloader._is_ad_reward_account_eligible(free_page)
        )

        for level in ("monthly", "semiannual", "annual"):
            paid_page = SimpleNamespace(
                authorized=True,
                account_info={"account_level": level},
            )
            self.assertFalse(
                VideoDownloader._is_ad_reward_account_eligible(paid_page)
            )

    def test_reward_entry_allows_web_login_when_client_is_logged_out(self):
        page = SimpleNamespace(authorized=False, account_info={})
        self.assertTrue(
            VideoDownloader._is_ad_reward_account_eligible(page)
        )

    def test_successful_reward_clears_web_login_state(self):
        endpoint = (
            Path(__file__).resolve().parents[1]
            / "license"
            / "claim_ad_reward.php"
        ).read_text(encoding="utf-8")

        self.assertIn("function reward_clear_web_login()", endpoint)
        self.assertIn("member_logout_session();", endpoint)
        self.assertEqual(endpoint.count("reward_clear_web_login();"), 2)
        self.assertGreaterEqual(endpoint.count("'web_login_cleared' => true"), 2)

    @patch("shouquan.platform.node", return_value="desktop-1")
    @patch("shouquan._post_api")
    @patch("shouquan.get_machine_code", return_value="machine-1")
    @patch("shouquan.get_saved_account_token", return_value="session-1")
    def test_create_reward_session_uses_authenticated_account(
        self,
        _saved_token,
        _machine_code,
        post_api,
        _node,
    ):
        post_api.return_value = {
            "status": "ok",
            "valid": True,
            "reward_url": "https://example.com/reward.php?token=abc",
            "reward_token": "abc",
            "reward_count": 3,
        }

        result = shouquan.create_ad_reward_session()

        self.assertTrue(result["valid"])
        payload = post_api.call_args.args[0]
        self.assertEqual(payload["action"], "create_ad_reward")
        self.assertEqual(payload["token"], "session-1")
        self.assertEqual(payload["machine_code"], "machine-1")

    @patch("shouquan.get_saved_account_token", return_value="")
    def test_create_reward_session_requires_login(self, _saved_token):
        result = shouquan.create_ad_reward_session()

        self.assertFalse(result["valid"])
        self.assertEqual(result["status"], "no_account")

    @patch("shouquan.save_account_session")
    @patch("shouquan._post_api")
    @patch("shouquan.get_machine_code", return_value="machine-1")
    @patch("shouquan.get_saved_account_token", return_value="session-1")
    def test_granted_reward_status_refreshes_local_account(
        self,
        _saved_token,
        _machine_code,
        post_api,
        save_session,
    ):
        post_api.return_value = {
            "status": "ok",
            "valid": True,
            "reward_status": "granted",
            "reward_count": 3,
            "today_download_remaining": 6,
        }

        result = shouquan.get_ad_reward_status("reward-1")

        self.assertTrue(result["valid"])
        self.assertEqual(result["reward_status"], "granted")
        save_session.assert_called_once_with(post_api.return_value)
        payload = post_api.call_args.args[0]
        self.assertEqual(payload["action"], "ad_reward_status")
        self.assertEqual(payload["reward_token"], "reward-1")


class SiteHeaderTests(unittest.TestCase):
    def test_public_pages_use_shared_navigation(self):
        project_root = Path(__file__).resolve().parents[1]
        pages = (
            "index.php",
            "features.php",
            "download.php",
            "subscribe.php",
            "member_login.php",
            "register.php",
            "reward.php",
            "payment.php",
            "manual_payment.php",
        )

        for page in pages:
            source = (project_root / "license" / page).read_text(encoding="utf-8")
            with self.subTest(page=page):
                self.assertIn("include/site_header.php", source)
                self.assertIn("render_site_header(", source)


class FreeCreditDisplayTests(unittest.TestCase):
    @patch("shouquan.save_auth_data", return_value=True)
    @patch(
        "shouquan.load_auth_data",
        return_value={
            "token": "session-1",
            "account_level": "semiannual",
            "expire_date": "2026-07-31 14:09:00",
        },
    )
    def test_free_downgrade_clears_cached_paid_expiry(
        self,
        _load_auth_data,
        save_auth_data,
    ):
        shouquan.save_account_session(
            {
                "account_level": "free",
                "account_level_label": "免费订阅",
                "quota_mode": "credit",
                "free_credit_balance": 3,
                "expire_date": "",
            }
        )

        payload = save_auth_data.call_args.args[0]
        self.assertEqual(payload["account_level"], "free")
        self.assertEqual(payload["expire_date"], "")
        self.assertEqual(payload["free_credit_balance"], 3)

    def test_free_account_uses_credit_balance_instead_of_daily_quota(self):
        page = SimpleNamespace(
            _is_account_subscription_expired=lambda account: False,
        )
        text = AboutPage._format_download_limit_text(
            page,
            {
                "quota_mode": "credit",
                "max_devices": 1,
                "per_task_limit": 1,
                "today_download_remaining": 5,
                "today_ad_reward_count": 3,
            },
        )

        self.assertEqual(
            text,
            "1 台 / 单次 1 个 / 免费额度剩余 5 次 / 今日免费领取 +3",
        )


class SuccessQualityLogTests(unittest.TestCase):
    def test_quality_label_supports_landscape_and_portrait_video(self):
        self.assertEqual(
            get_quality_label({"resolution": "1920x1080"}),
            "1080P",
        )
        self.assertEqual(
            get_quality_label({"resolution": "1080x1920"}),
            "1080P",
        )
        self.assertEqual(
            get_quality_label({"resolution": "3840x2160"}),
            "2160P",
        )

    def test_success_message_includes_detected_quality(self):
        message = get_success_message(
            "TikTok",
            cookie_used=False,
            selected_format={"resolution": "1080x1920"},
        )

        self.assertEqual(
            message,
            "下载成功 | TikTok | 画质：1080P | 未走 Cookie",
        )


class ThumbnailCommandTests(unittest.TestCase):
    def _runtime(self, write_thumbnail):
        return DownloadRuntime(
            yt_dlp_path="yt-dlp.exe",
            ffmpeg_path="ffmpeg.exe",
            save_path="downloads",
            write_thumbnail=write_thumbnail,
        )

    def test_all_platform_commands_write_thumbnail_when_enabled(self):
        downloaders = [
            YouTubeDownloader(self._runtime(True)),
            TikTokDownloader(self._runtime(True)),
            InstagramDownloader(self._runtime(True)),
            TwitterDownloader(self._runtime(True)),
        ]

        for downloader in downloaders:
            with self.subTest(platform=downloader.platform_name):
                self.assertIn("--write-thumbnail", downloader._build_base_command())

    def test_thumbnail_command_is_opt_in(self):
        downloader = YouTubeDownloader(self._runtime(False))

        self.assertNotIn("--write-thumbnail", downloader._build_base_command())


class CurrentVideoProgressTests(unittest.TestCase):
    def _build_page_stub(self):
        page = SimpleNamespace(
            lbl_total_tasks=_LabelRecorder(),
            lbl_completed_tasks=_LabelRecorder(),
            lbl_failed_tasks=_LabelRecorder(),
            lbl_download_percent=_LabelRecorder(),
            lbl_speed=_LabelRecorder(),
            current_video_title="等待下载...",
            current_video_progress=0,
            current_downloaded_size="0 B",
            current_total_size="0 B",
            current_speed="0 KB/s",
            current_eta="00:00",
        )
        page.reset_current_progress = lambda: VideoDownloadPage.reset_current_progress(page)
        return page

    def test_task_stats_do_not_replace_current_video_percent(self):
        page = self._build_page_stub()
        page.lbl_download_percent.setText("下载百分比：42.0%")

        VideoDownloadPage.update_task_stats(page, 3, 1, 0)

        self.assertEqual(page.lbl_download_percent.text, "下载百分比：42.0%")
        self.assertEqual(page.lbl_completed_tasks.text, "完成：1")

    def test_each_video_starts_at_zero_and_success_stays_at_100(self):
        page = self._build_page_stub()

        VideoDownloadPage.update_current_progress(
            page,
            {"type": "progress", "data": {"percent": 63.5, "speed": "2.1MiB/s"}},
        )
        self.assertEqual(page.lbl_download_percent.text, "下载百分比：63.5%")

        VideoDownloadPage.update_current_progress(
            page,
            {"type": "complete", "data": {"success": True}},
        )
        self.assertEqual(page.lbl_download_percent.text, "下载百分比：100.0%")

        VideoDownloadPage.update_current_progress(
            page,
            {"type": "start", "data": {}},
        )
        self.assertEqual(page.lbl_download_percent.text, "下载百分比：0.0%")
        self.assertEqual(page.lbl_speed.text, "速度：0 KB/s")


class OutputValidationTests(unittest.TestCase):
    def test_only_accepts_non_empty_final_video_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            valid = os.path.join(temp_dir, "video.mp4")
            empty = os.path.join(temp_dir, "empty.mp4")
            partial = os.path.join(temp_dir, "video.part")
            with open(valid, "wb") as stream:
                stream.write(b"video-data")
            open(empty, "wb").close()
            with open(partial, "wb") as stream:
                stream.write(b"partial-data")

            output = "\n".join([
                f"[VIDOON_OUTPUT] {valid}",
                f"[VIDOON_OUTPUT] {valid}",
                f"[VIDOON_OUTPUT] {empty}",
                f"[VIDOON_OUTPUT] {partial}",
            ])
            parsed = extract_output_file_paths(output)

            self.assertEqual(parsed, [valid, empty, partial])
            self.assertEqual(validate_output_files(parsed), [os.path.abspath(valid)])

    def test_zero_exit_without_current_output_is_failure(self):
        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        downloader = SimpleNamespace(
            runtime=runtime,
            platform_name="YouTube",
            _parse_progress=lambda line: None,
            classify_error=lambda text: "UNKNOWN",
        )
        command_result = {
            "returncode": 0,
            "raw_stdout": "",
            "raw_stderr": "",
            "output_text": "",
            "selected_format": None,
            "output_files": [],
            "output_ids": [],
            "cancelled": False,
        }
        with patch("core.download_utils.run_command", return_value=command_result):
            result = run_logged_download_strategy(
                downloader,
                ["yt-dlp"],
                "https://example.com/video",
                lambda message: None,
                "PlatformNoCookie",
                cookie_used=False,
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_source"], "OUTPUT_NOT_FOUND")

    def test_falls_back_to_video_id_when_unicode_path_is_misdecoded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_id = "7647834207573560589"
            actual_path = os.path.join(temp_dir, f"unicode-title-{video_id}.mp4")
            with open(actual_path, "wb") as stream:
                stream.write(b"video-data")

            output = (
                f"[VIDOON_OUTPUT] {os.path.join(temp_dir, 'garbled-title-' + video_id + '.mp4')}\n"
                f"[VIDOON_OUTPUT_ID] {video_id}"
            )
            output_ids = extract_output_ids(output)
            result = validate_output_files(
                extract_output_file_paths(output),
                output_ids=output_ids,
                search_dir=temp_dir,
            )

            self.assertEqual(result, [os.path.abspath(actual_path)])


class StrategyTests(unittest.TestCase):
    def test_youtube_normalizes_long_and_shared_urls_to_one_video(self):
        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        downloader = YouTubeDownloader(runtime)
        long_url = (
            "https://www.youtube.com/watch?v=LfLz_-kGP3U"
            "&list=RDLfLz_-kGP3U&start_radio=1"
        )
        shared_url = "https://youtu.be/LfLz_-kGP3U?si=tracking"

        long_result = downloader.normalize_url(long_url)
        shared_result = downloader.normalize_url(shared_url)

        expected = "https://www.youtube.com/watch?v=LfLz_-kGP3U"
        self.assertEqual(long_result["normalized_url"], expected)
        self.assertEqual(shared_result["normalized_url"], expected)

    def test_instagram_plural_reels_path_is_recognized(self):
        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        downloader = InstagramDownloader(runtime)
        result = downloader.normalize_url("https://www.instagram.com/reels/DbVTRT0x31j/")

        self.assertTrue(result["is_shorts"])

    def test_deno_rejects_original_url_and_accepts_new_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            deno_path = os.path.join(temp_dir, "deno.exe")
            open(deno_path, "wb").close()
            resolver = DenoResolver(deno_path, 1)
            source_url = "https://example.com/watch/1"

            same_url_result = SimpleNamespace(
                returncode=0,
                stdout=f"video_url: {source_url}",
                stderr="",
            )
            with patch("setting.subprocess.run", return_value=same_url_result):
                self.assertIsNone(resolver.resolve_url(source_url))

            media_url = "https://cdn.example.com/video.mp4"
            media_result = SimpleNamespace(
                returncode=0,
                stdout=f"video_url: {media_url}",
                stderr="",
            )
            with patch("setting.subprocess.run", return_value=media_result):
                self.assertEqual(resolver.resolve_url(source_url)["video_url"], media_url)

    def test_youtube_bot_check_tries_cookie_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_path = os.path.join(temp_dir, "cookies.txt")
            open(cookie_path, "w").close()
            runtime = DownloadRuntime(
                "yt-dlp",
                "ffmpeg",
                temp_dir,
                cookie_file=cookie_path,
            )
            downloader = YouTubeDownloader(runtime)
            bot_failure = build_result(
                success=False,
                platform="YouTube",
                strategy_used="PlatformNoCookie",
                error_source="AUTH_BOT_CHECK",
            )
            cookie_success = build_result(
                success=True,
                platform="YouTube",
                strategy_used="PlatformCookie",
                cookie_used=True,
            )
            cookie_calls = []
            downloader._download_platform_without_cookie = lambda *args: bot_failure
            downloader._download_platform_with_cookie = (
                lambda *args: cookie_calls.append(True) or cookie_success
            )

            result = downloader.execute_download(
                {"normalized_url": "https://youtube.com/watch?v=1", "is_shorts": False},
                lambda message: None,
            )

            self.assertTrue(result["success"])
            self.assertEqual(cookie_calls, [True])

    def test_youtube_403_uses_provider_before_720p_hls_fallback(self):
        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        downloader = YouTubeDownloader(runtime)
        http_403 = build_result(
            success=False,
            platform="YouTube",
            strategy_used="PlatformNoCookie",
            error_source="YOUTUBE_HTTP_403",
        )
        hls_success = build_result(
            success=True,
            platform="YouTube",
            strategy_used="YouTubeHlsFallback",
        )
        fallback_calls = []
        provider_calls = []
        downloader._download_platform_without_cookie = lambda *args: http_403
        downloader._download_with_pot_provider = (
            lambda *args, **kwargs: provider_calls.append(kwargs) or None
        )
        downloader._download_hls_fallback = (
            lambda *args, **kwargs: fallback_calls.append(kwargs) or hls_success
        )
        http_403["selected_format"] = {"resolution": "1920x1080"}

        result = downloader.execute_download(
            {"normalized_url": "https://youtube.com/watch?v=1", "is_shorts": False},
            lambda message: None,
        )

        self.assertTrue(result["success"])
        self.assertEqual(provider_calls, [{"quality_axis": "height"}])
        self.assertEqual(
            fallback_calls,
            [{"quality_axis": "height", "minimum_quality": 720}],
        )

    def test_youtube_403_returns_provider_success_without_hls(self):
        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        downloader = YouTubeDownloader(runtime)
        http_403 = build_result(
            success=False,
            platform="YouTube",
            strategy_used="PlatformNoCookie",
            error_source="YOUTUBE_HTTP_403",
            selected_format={"resolution": "1920x1080"},
        )
        provider_success = build_result(
            success=True,
            platform="YouTube",
            strategy_used="YouTubePoToken",
        )
        downloader._download_platform_without_cookie = lambda *args: http_403
        downloader._download_with_pot_provider = lambda *args, **kwargs: provider_success
        downloader._download_hls_fallback = lambda *args, **kwargs: self.fail(
            "HLS should not run after Provider succeeds"
        )

        result = downloader.execute_download(
            {"normalized_url": "https://youtube.com/watch?v=1", "is_shorts": False},
            lambda message: None,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["strategy_used"], "YouTubePoToken")

    def test_youtube_fallback_commands_change_protocol_or_stream_type(self):
        runtime = DownloadRuntime(
            "yt-dlp",
            "ffmpeg",
            tempfile.gettempdir(),
            enable_deno=True,
            deno_path="deno",
        )
        downloader = YouTubeDownloader(runtime)

        standard = downloader._build_youtube_command(
            "https://youtube.com/watch?v=1",
            False,
            use_cookie=False,
        )
        hls = downloader._build_youtube_command(
            "https://youtube.com/watch?v=1",
            False,
            use_cookie=False,
            fallback_mode="hls",
            minimum_quality=720,
        )
        pot = downloader._build_youtube_command(
            "https://youtube.com/watch?v=1",
            False,
            use_cookie=False,
            fallback_mode="pot",
            minimum_quality=720,
            quality_axis="height",
            provider_base_url="http://127.0.0.1:4416",
        )
        portrait_axis, portrait_quality = downloader._selected_quality_requirement(
            {"resolution": "1080x1920"}
        )

        self.assertIn("--continue", standard)
        self.assertIn("--no-plugin-dirs", standard)
        self.assertNotIn("--no-plugin-dirs", pot)
        self.assertNotIn("--no-continue", standard)
        self.assertNotIn("--force-overwrites", standard)
        self.assertNotEqual(standard[standard.index("-f") + 1], hls[hls.index("-f") + 1])
        self.assertIn("protocol^=m3u8", hls[hls.index("-f") + 1])
        self.assertIn("player_client=web_safari", hls[hls.index("--extractor-args") + 1])
        self.assertIn("[height>=720]", pot[pot.index("-f") + 1])
        self.assertIn("player_client=mweb", pot[pot.index("--extractor-args") + 1])
        self.assertIn("--plugin-dirs", pot)
        self.assertTrue(
            any("youtubepot-bgutilhttp:base_url=" in value for value in pot)
        )
        self.assertEqual((portrait_axis, portrait_quality), ("width", 1080))

    def test_youtube_fallback_never_selects_below_720p(self):
        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        downloader = YouTubeDownloader(runtime)
        hls = downloader._build_youtube_command(
            "https://youtube.com/watch?v=1",
            False,
            use_cookie=False,
            fallback_mode="hls",
            minimum_quality=720,
            quality_axis="height",
        )

        self.assertIn("[height>=720]", hls[hls.index("-f") + 1])
        self.assertNotIn("height<=", hls[hls.index("-f") + 1])

        standard = downloader._build_youtube_command(
            "https://youtube.com/watch?v=1",
            False,
            use_cookie=False,
        )
        shorts = downloader._build_youtube_command(
            "https://youtube.com/shorts/1",
            True,
            use_cookie=False,
        )
        self.assertTrue(standard[standard.index("-f") + 1].endswith("best[height>=720]"))
        self.assertTrue(shorts[shorts.index("-f") + 1].endswith("best[width>=720]"))

    def test_youtube_component_progress_never_moves_backwards(self):
        progress_events = []
        runtime = DownloadRuntime(
            "yt-dlp",
            "ffmpeg",
            tempfile.gettempdir(),
            progress_callback=lambda type_, data: progress_events.append((type_, data)),
        )
        downloader = YouTubeDownloader(runtime)

        downloader._parse_progress(
            "[download] 100.0% of 83.78MiB at 653.54KiB/s ETA 00:00"
        )
        downloader._parse_progress(
            "[download] 33.5% of 2.98MiB at 879.18KiB/s ETA 00:02"
        )

        self.assertEqual(
            [event[1]["percent"] for event in progress_events if event[0] == "progress"],
            [100.0],
        )


class ErrorClassificationTests(unittest.TestCase):
    def test_precise_classification_avoids_broad_keywords(self):
        youtube = YouTubeDownloader.__new__(YouTubeDownloader)
        tiktok = TikTokDownloader.__new__(TikTokDownloader)
        instagram = InstagramDownloader.__new__(InstagramDownloader)
        twitter = TwitterDownloader.__new__(TwitterDownloader)

        self.assertEqual(youtube.classify_error("general network metadata"), "UNKNOWN")
        self.assertEqual(
            youtube.classify_error(
                "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            ),
            "YOUTUBE_HTTP_403",
        )
        self.assertEqual(tiktok.classify_error("curl: (35) TLS connect error"), "NETWORK_ERROR")
        self.assertEqual(
            instagram.classify_error("HTTP Error 429: Too Many Requests"),
            "NETWORK_RATE_LIMIT",
        )
        self.assertEqual(
            twitter.classify_error("This tweet is unavailable"),
            "CONTENT_UNAVAILABLE",
        )
        self.assertFalse(twitter.should_use_cookie("NETWORK_RATE_LIMIT"))

    def test_rate_limit_and_invalid_cookie_do_not_trigger_more_strategies(self):
        platform_cases = (
            (TikTokDownloader, "TikTok"),
            (InstagramDownloader, "Instagram"),
            (TwitterDownloader, "Twitter"),
        )
        for downloader_class, platform_name in platform_cases:
            with self.subTest(platform=platform_name):
                runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
                downloader = downloader_class(runtime)
                rate_limit = build_result(
                    success=False,
                    platform=platform_name,
                    strategy_used="PlatformNoCookie",
                    error_source="NETWORK_RATE_LIMIT",
                )
                downloader._download_platform_without_cookie = lambda *args: rate_limit
                downloader._download_platform_with_cookie = (
                    lambda *args: self.fail("rate limit must not use cookies")
                )
                result = downloader.execute_download(
                    {
                        "normalized_url": "https://example.com/status/1",
                        "url_modified": False,
                    },
                    lambda message: None,
                )
                self.assertEqual(result["error_source"], "NETWORK_RATE_LIMIT")

        runtime = DownloadRuntime("yt-dlp", "ffmpeg", tempfile.gettempdir())
        youtube = YouTubeDownloader(runtime)
        invalid_cookie = build_result(
            success=False,
            platform="YouTube",
            strategy_used="PlatformNoCookie",
            error_source="AUTH_COOKIE_INVALID",
        )
        youtube._download_platform_without_cookie = lambda *args: invalid_cookie
        youtube._download_platform_with_cookie = (
            lambda *args: self.fail("invalid cookie must stop immediately")
        )
        result = youtube.execute_download(
            {
                "normalized_url": "https://youtube.com/watch?v=1",
                "is_shorts": False,
            },
            lambda message: None,
        )
        self.assertEqual(result["error_source"], "AUTH_COOKIE_INVALID")

    def test_auth_required_uses_each_platform_cookie_once(self):
        platform_cases = (
            (TikTokDownloader, "TikTok", "tiktok_cookie_file"),
            (InstagramDownloader, "Instagram", "instagram_cookie_file"),
            (TwitterDownloader, "Twitter", "twitter_cookie_file"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for downloader_class, platform_name, cookie_field in platform_cases:
                with self.subTest(platform=platform_name):
                    cookie_path = os.path.join(temp_dir, f"{platform_name}.txt")
                    open(cookie_path, "w").close()
                    runtime = DownloadRuntime("yt-dlp", "ffmpeg", temp_dir)
                    setattr(runtime, cookie_field, cookie_path)
                    downloader = downloader_class(runtime)
                    auth_required = build_result(
                        success=False,
                        platform=platform_name,
                        strategy_used="PlatformNoCookie",
                        error_source="AUTH_NEED_LOGIN",
                    )
                    cookie_success = build_result(
                        success=True,
                        platform=platform_name,
                        strategy_used="PlatformCookie",
                        cookie_used=True,
                    )
                    cookie_calls = []
                    downloader._download_platform_without_cookie = lambda *args: auth_required
                    downloader._download_platform_with_cookie = (
                        lambda *args: cookie_calls.append(True) or cookie_success
                    )

                    result = downloader.execute_download(
                        {
                            "normalized_url": "https://example.com/status/1",
                            "url_modified": False,
                        },
                        lambda message: None,
                    )

                    self.assertTrue(result["success"])
                    self.assertEqual(cookie_calls, [True])


class ProcessCancellationTests(unittest.TestCase):
    def test_cancel_stops_running_process(self):
        cancel_event = threading.Event()
        timer = threading.Timer(0.3, cancel_event.set)
        timer.start()
        started = time.monotonic()
        result = run_command(
            [
                sys.executable,
                "-c",
                "import time; print('started', flush=True); time.sleep(30)",
            ],
            cancel_checker=cancel_event.is_set,
        )
        elapsed = time.monotonic() - started

        self.assertTrue(result["cancelled"])
        self.assertLess(elapsed, 5)


class ProgressLoggingTests(unittest.TestCase):
    def test_duplicate_completed_progress_is_suppressed(self):
        messages = []
        runtime = SimpleNamespace(log_callback=messages.append)
        progress = {
            "percent": 100.0,
            "downloaded": "1.0MiB",
            "total": "1.0MiB",
            "speed": "1.0MiB/s",
            "eta": "00:00",
        }

        maybe_log_download_progress(runtime, "Twitter", "PlatformNoCookie", progress)
        maybe_log_download_progress(runtime, "Twitter", "PlatformNoCookie", progress)

        self.assertEqual(len(messages), 1)


class ToolUpdateNoticeTests(unittest.TestCase):
    def test_yt_dlp_outdated_warning_gets_chinese_notice_and_official_url(self):
        notices = detect_tool_update_notices(
            "WARNING: Your yt-dlp version (2026.03.17) is older than 90 days!"
        )

        self.assertEqual(len(notices), 1)
        self.assertIn("检测到 yt-dlp 版本过旧或不兼容", notices[0])
        self.assertIn("https://github.com/yt-dlp/yt-dlp/releases/latest", notices[0])

    def test_normal_download_output_does_not_show_update_notice(self):
        self.assertEqual(
            detect_tool_update_notices("Downloading 1 format(s): 137+140"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
