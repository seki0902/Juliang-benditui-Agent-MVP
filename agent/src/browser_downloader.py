"""Read-only browser downloader for Local Push exports.

The page-specific selectors live in a JSON config file because the Local Push
account may expose different report tabs and date-picker markup. This module
never clicks controls that change delivery, budget, bid, plan status, or
creative publication state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse
from run_observer import step, ACTIVE


class BrowserDownloadError(RuntimeError):
    """Raised when a report cannot be downloaded and validated."""


@dataclass(frozen=True)
class ReportSpec:
    kind: str
    name: str
    url: str
    tab_selector: str | None
    date_start_selector: str
    date_end_selector: str
    query_selector: str
    export_selector: str
    ready_selector: str | None = None
    file_prefix: str | None = None
    date_shortcut_selector: str | None = None
    secondary_tab_selector: str | None = None


@dataclass(frozen=True)
class BrowserDownloadConfig:
    profile_dir: Path
    profile_name: str
    output_dir: Path
    report_url: str
    reports: tuple[ReportSpec, ...]
    source_profile_dir: Path | None = None
    copy_profile: bool = False
    browser_channel: str = "chrome"
    headless: bool = False
    timeout_ms: int = 60000
    date_mode: str = "mtd_to_yesterday"
    timezone: str = "Asia/Shanghai"
    login_url_contains: str = "/login"
    login_selector: str | None = None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if value else None


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrowserDownloadError(f"浏览器下载配置缺少 {label}")
    return value.strip()


def load_browser_config(path: Path) -> BrowserDownloadConfig:
    """Load browser and report selectors from a JSON file plus environment."""
    if not path.exists():
        raise BrowserDownloadError(f"浏览器下载配置不存在: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BrowserDownloadError(f"浏览器下载配置不是有效 JSON: {path}") from exc

    browser = raw.get("browser", {})
    profile_dir = _env("BROWSER_PROFILE_DIR") or browser.get("profile_dir")
    source_profile_dir = _env("BROWSER_SOURCE_PROFILE_DIR") or browser.get("source_profile_dir")
    profile_name = _env("BROWSER_PROFILE_NAME") or browser.get("profile_name") or "Default"
    report_url = _env("LOCAL_PUSH_REPORT_URL") or browser.get("report_url")
    output_dir = _env("DOWNLOAD_DIR") or browser.get("output_dir") or "./inputs"
    if not profile_dir:
        raise BrowserDownloadError("未配置 BROWSER_PROFILE_DIR；请提供已登录浏览器配置目录")
    if not report_url:
        raise BrowserDownloadError("未配置 LOCAL_PUSH_REPORT_URL 或 browser.report_url")

    reports: list[ReportSpec] = []
    for item in raw.get("reports", []):
        reports.append(
            ReportSpec(
                kind=_required_string(item.get("kind"), "reports[].kind"),
                name=_required_string(item.get("name"), "reports[].name"),
                url=str(item.get("url") or report_url),
                tab_selector=item.get("tab_selector"),
                date_start_selector=_required_string(item.get("date_start_selector"), "reports[].date_start_selector"),
                date_end_selector=_required_string(item.get("date_end_selector"), "reports[].date_end_selector"),
                query_selector=str(item.get("query_selector") or ""),
                export_selector=_required_string(item.get("export_selector"), "reports[].export_selector"),
                ready_selector=item.get("ready_selector"),
                file_prefix=item.get("file_prefix"),
                date_shortcut_selector=item.get("date_shortcut_selector"),
                secondary_tab_selector=item.get("secondary_tab_selector"),
            )
        )
    if not reports:
        raise BrowserDownloadError("浏览器下载配置至少需要一个 reports 项")

    timeout_seconds = int(_env("BROWSER_TIMEOUT_SECONDS") or browser.get("timeout_seconds") or 60)
    def config_path(value: str) -> Path:
        resolved = Path(value).expanduser()
        return (path.resolve().parent / resolved).resolve()

    return BrowserDownloadConfig(
        profile_dir=config_path(profile_dir),
        profile_name=str(profile_name),
        output_dir=config_path(output_dir),
        report_url=str(report_url),
        reports=tuple(reports),
        source_profile_dir=config_path(source_profile_dir) if source_profile_dir else None,
        copy_profile=_bool(_env("BROWSER_COPY_PROFILE"), _bool(browser.get("copy_profile"), False)),
        browser_channel=str(_env("BROWSER_CHANNEL") or browser.get("channel") or "chrome"),
        headless=_bool(_env("BROWSER_HEADLESS"), _bool(browser.get("headless"), False)),
        timeout_ms=max(timeout_seconds, 10) * 1000,
        date_mode=str(_env("REPORT_DATE_MODE") or browser.get("date_mode") or "mtd_to_yesterday"),
        timezone=str(_env("TIMEZONE") or browser.get("timezone") or "Asia/Shanghai"),
        login_url_contains=str(browser.get("login_url_contains") or "/login"),
        login_selector=browser.get("login_selector"),
    )


def resolve_date_range(mode: str = "mtd_to_yesterday", now: datetime | None = None, timezone: str = "Asia/Shanghai") -> tuple[date, date]:
    """Return the report range required by the V3 daily workflow."""
    now = now or datetime.now(ZoneInfo(timezone))
    yesterday = (now - timedelta(days=1)).date()
    if mode == "yesterday":
        return yesterday, yesterday
    if mode == "mtd_to_yesterday":
        return yesterday.replace(day=1), yesterday
    raise BrowserDownloadError(f"不支持的 REPORT_DATE_MODE: {mode}")


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", value, flags=re.UNICODE).strip("_")
    return value or "report"


def _ensure_download_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _ignore_profile_entries(_directory: str, names: list[str]) -> set[str]:
    ignored = {
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnCache",
        "GrShaderCache",
        "ShaderCache",
        "Service Worker\\CacheStorage",
        "LOCK",
        "SingletonCookie",
        "SingletonLock",
        "SingletonSocket",
    }
    return {name for name in names if name in ignored or name.startswith("Singleton")}


def _prepare_profile(config: BrowserDownloadConfig) -> None:
    """Create the automation profile once from the user's logged-in profile."""
    if config.source_profile_dir and config.profile_dir.resolve() == config.source_profile_dir.resolve():
        raise BrowserDownloadError("自动化目录不能使用日常 Chrome 用户目录")
    if not config.copy_profile:
        return
    if (config.profile_dir / "profile_initialized.json").exists():
        return
    if config.profile_dir.exists():
        raise BrowserDownloadError(f"自动化目录已存在但没有初始化完成标记，请人工检查：{config.profile_dir}")
    if not config.source_profile_dir or not config.source_profile_dir.exists():
        raise BrowserDownloadError(
            f"自动化配置目录不存在，且没有可复制的 Chrome 源目录: {config.profile_dir}"
        )
    source_profile = config.source_profile_dir / config.profile_name
    if not source_profile.exists():
        raise BrowserDownloadError(f"Chrome 源 Profile 不存在: {source_profile}")

    config.profile_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="profile-copy-", dir=config.profile_dir.parent))
    try:
        shutil.copy2(config.source_profile_dir / "Local State", staging / "Local State")
        destination = staging / config.profile_name
        destination.mkdir()
        for relative in ("Preferences", "Secure Preferences", "Network/Cookies",
                         "Network/Cookies-journal", "Cookies", "Cookies-journal",
                         "Local Storage", "Session Storage"):
            source = source_profile / relative
            target = destination / relative
            if not source.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, ignore=_ignore_profile_entries)
            else:
                shutil.copy2(source, target)
        (staging / "profile_initialized.json").write_text(
            json.dumps({"created_at": datetime.now().astimezone().isoformat()}), encoding="utf-8")
        staging.rename(config.profile_dir)
    except OSError as exc:
        raise BrowserDownloadError(
            f"登录配置复制未完成（{type(exc).__name__}）；请人工关闭日常 Chrome 后重试。"
            f"源目录未修改；未完成副本保留在 {staging}"
        ) from exc


def _validate_download(path: Path, expected_suffix: str | None = None) -> None:
    if not path.exists():
        raise BrowserDownloadError(f"下载文件未落盘: {path}")
    if path.stat().st_size <= 0:
        raise BrowserDownloadError(f"下载文件为空: {path}")
    if expected_suffix and path.suffix.lower() not in {".xlsx", ".xls", ".csv"}:
        raise BrowserDownloadError(f"下载文件类型不是 xlsx/xls/csv: {path.name}")


def _import_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserDownloadError(
            "当前 Python 环境没有 playwright 包；请在实际运行环境安装 playwright，"
            "并准备可复用的已登录浏览器配置目录。"
        ) from exc
    return sync_playwright


def _download_one(page: Any, spec: ReportSpec, start_date: date, end_date: date,
                  output_dir: Path, timeout_ms: int, login_url_contains: str,
                  login_selector: str | None) -> Path:
    page.goto(spec.url, wait_until="domcontentloaded", timeout=timeout_ms)
    account = parse_qs(urlparse(spec.url).query).get("advid", [None])[0]
    if account:
        page.get_by_role("button").filter(has_text=f"ID：{account}").wait_for(timeout=timeout_ms)
    if login_url_contains and login_url_contains in page.url:
        raise BrowserDownloadError(f"浏览器登录态失效，当前页面为登录页: {page.url}")
    if login_selector and page.locator(login_selector).count() > 0:
        raise BrowserDownloadError("浏览器登录态失效，检测到登录控件")
    if spec.tab_selector:
        page.locator(spec.tab_selector).click(timeout=timeout_ms)
    if spec.secondary_tab_selector:
        page.locator(spec.secondary_tab_selector).click(timeout=timeout_ms)
    if spec.date_shortcut_selector:
        if start_date != end_date:
            raise BrowserDownloadError("昨日快捷日期仅支持单日报表")
        page.locator(spec.date_start_selector).click(timeout=timeout_ms)
        page.locator(spec.date_shortcut_selector).click(timeout=timeout_ms)
    else:
        # 巨量本地推的日期输入框是 readonly；必须用日历控件选择。
        # “本月”先将开始日定位至当月 1 日，之后按日期单元格的真实
        # title 精确选择结束日，避免将尚未稳定的当日数据纳入日报。
        if start_date.day != 1 or start_date.year != end_date.year or start_date.month != end_date.month:
            raise BrowserDownloadError("当前日期选择器仅校准了同月 1 日至指定结束日的导出")
        page.locator(spec.date_start_selector).click(timeout=timeout_ms)
        page.get_by_text("本月", exact=True).click(timeout=timeout_ms)
        page.locator(spec.date_end_selector).click(timeout=timeout_ms)
        page.locator(f"span[title='{end_date.isoformat()}']").click(timeout=timeout_ms)
    from playwright.sync_api import expect
    expect(page.locator(spec.date_start_selector)).to_have_value(start_date.isoformat(), timeout=timeout_ms)
    expect(page.locator(spec.date_end_selector)).to_have_value(end_date.isoformat(), timeout=timeout_ms)
    if spec.query_selector:
        page.locator(spec.query_selector).click(timeout=timeout_ms)
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
    except Exception:
        # Some data pages keep a polling connection open. The ready selector
        # below, when configured, is the stronger completion signal.
        pass
    if spec.ready_selector:
        page.locator(spec.ready_selector).first.wait_for(state="visible", timeout=timeout_ms)

    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.locator(spec.export_selector).first.click(timeout=timeout_ms)
        download = download_info.value
        suggested = download.suggested_filename or f"{spec.kind}_{end_date.isoformat()}.xlsx"
        suffix = Path(suggested).suffix.lower() or ".xlsx"
        prefix = _safe_filename(spec.file_prefix or spec.kind)
        target = output_dir / f"{prefix}_{end_date.isoformat()}{suffix}"
        download.save_as(str(target))
    except Exception as exc:
        closed = page.is_closed()
        raise BrowserDownloadError(f"{spec.name} 下载失败：{exc}；页面已关闭={closed}") from exc
    _validate_download(target, suffix)
    return target


def download_reports(config: BrowserDownloadConfig, now: datetime | None = None) -> dict[str, Any]:
    """Retry only transient browser/download failures, never downstream writes."""
    for attempt in range(1, 4):
        try:
            result = step(f'第 {attempt} 次下载尝试')(_download_reports_once)(config, now)
            result['download_attempts'] = attempt
            observer = ACTIVE.get()
            if observer and attempt > 1:
                for item in observer.data['steps']:
                    if item['status'] == 'failed' and (item['name'].startswith('保存') or '次下载尝试' in item['name']):
                        item['status'] = 'recovered'
                observer.save()
            return result
        except BrowserDownloadError as exc:
            transient = any(text in str(exc).lower() for text in (
                'target page, context or browser has been closed', 'net::err_',
                'download.save_as', 'download canceled', 'download cancelled',
            ))
            if not transient or attempt == 3:
                raise
            time.sleep(attempt * 3)
    raise AssertionError('unreachable')


def _download_reports_once(config: BrowserDownloadConfig, now: datetime | None = None) -> dict[str, Any]:
    """Download all configured reports and return a traceable manifest.

    The function raises before generating a success manifest if login is
    invalid, a selector fails, or a downloaded file is empty.
    """
    _prepare_profile(config)
    if not config.profile_dir.exists():
        raise BrowserDownloadError(f"浏览器配置目录不存在: {config.profile_dir}")
    start_date, end_date = resolve_date_range(config.date_mode, now, config.timezone)
    _ensure_download_dir(config.output_dir)
    sync_playwright = _import_playwright()
    files: list[dict[str, Any]] = []

    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, Any] = {
                "user_data_dir": str(config.profile_dir),
                "headless": config.headless,
                "accept_downloads": True,
                "args": [f"--profile-directory={config.profile_name}"],
            }
            if config.browser_channel:
                launch_options["channel"] = config.browser_channel
            # Reusing a persistent Chrome download profile can crash the browser
            # during save_as on Windows. Read its authenticated storage in memory,
            # then perform exports in a fresh, isolated browser context. Never
            # persist cookies or alter the operator's login profile.
            login_context = playwright.chromium.launch_persistent_context(**launch_options)
            try:
                storage = login_context.storage_state()
            finally:
                login_context.close()
            ephemeral_options = {'headless': config.headless}
            if config.browser_channel:
                ephemeral_options['channel'] = config.browser_channel
            browser = playwright.chromium.launch(**ephemeral_options)
            context = browser.new_context(storage_state=storage, accept_downloads=True)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                for spec in config.reports:
                    target = step(f'保存{spec.name}')(_download_one)(
                        page, spec, start_date, end_date, config.output_dir, config.timeout_ms,
                        config.login_url_contains, config.login_selector,
                    )
                    files.append({
                        "kind": spec.kind,
                        "name": spec.name,
                        "path": str(target),
                        "size": target.stat().st_size,
                    })
            finally:
                context.close()
                browser.close()
    except BrowserDownloadError:
        raise
    except Exception as exc:
        raise BrowserDownloadError(
            "浏览器下载未完成；请检查登录态、浏览器配置目录是否被占用，以及页面选择器配置。"
            f" 原因：{exc}"
        ) from exc

    fetched_at = datetime.now(ZoneInfo(config.timezone)).isoformat()
    return {
        "account_id": _env("LOCAL_PUSH_ACCOUNT_ID") or _env("OCEANENGINE_LOCAL_ACCOUNT_ID") or parse_qs(urlparse(config.report_url).query).get("advid", [None])[0],
        "data_date": end_date.isoformat(),
        "date_start": start_date.isoformat(),
        "date_end": end_date.isoformat(),
        "fetched_at": fetched_at,
        "sources": [f"browser_export_{item['kind']}" for item in files],
        "status": "success",
        "raw_records": None,
        "files": files,
        "errors": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="通过已登录浏览器只读下载本地推报表")
    parser.add_argument("--config", type=Path, required=True, help="浏览器下载配置 JSON")
    parser.add_argument("--output", type=Path, help="覆盖配置中的下载目录")
    args = parser.parse_args()
    config = load_browser_config(args.config)
    if args.output:
        config = BrowserDownloadConfig(**{**config.__dict__, "output_dir": args.output})
    manifest = download_reports(config)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
