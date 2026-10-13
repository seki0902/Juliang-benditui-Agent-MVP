"""Safe daily aggregate write to the user's logged-in Shimo sheet."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from browser_downloader import load_browser_config, _import_playwright


class ShimoSheetError(RuntimeError):
    pass


ROOT = Path(__file__).resolve().parents[1]


def _load_config(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "shimo_sheet_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _ready(page: Any, selectors: dict[str, str], expected_sheet: str) -> None:
    page.locator(selectors["name_box"]).wait_for(state="visible", timeout=60000)
    active = page.locator(selectors["active_sheet"])
    active.wait_for(state="visible", timeout=60000)
    page.wait_for_timeout(1500)
    if active.inner_text().strip() != expected_sheet:
        raise ShimoSheetError(f"当前工作表不是 {expected_sheet}")


def _select(page: Any, selectors: dict[str, str], address: str) -> str:
    box = page.locator(selectors["name_box"])
    box.click()
    box.press("Control+a")
    # Match the verified read path. Faster input sometimes leaves Shimo's
    # name box one cell behind while the canvas is still handling selection.
    box.press_sequentially(address, delay=80)
    box.press("Enter")
    page.wait_for_timeout(200)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and box.input_value().upper() != address.upper():
        page.wait_for_timeout(120)
    if box.input_value().upper() != address.upper():
        raise ShimoSheetError(f"选区错位：期望 {address}，实际 {box.input_value()}")
    return page.locator(selectors["formula_bar"]).inner_text().rstrip("\n")


def _set_value(page: Any, selectors: dict[str, str], address: str, value: str) -> None:
    _select(page, selectors, address)
    editor = page.locator(selectors["formula_bar"])
    editor.click()
    editor.press("Control+a")
    editor.press_sequentially(value, delay=25)
    editor.press("Enter")
    page.wait_for_timeout(1200)
    if _select(page, selectors, address) != value:
        raise ShimoSheetError(f"写后读回不一致：{address}")


def _date_value(value: str) -> str:
    return value.strip().replace("-", "/")


def _number_or_none(value: str) -> int | float | None:
    value = value.strip().replace(",", "")
    if not value or value in {"-", "—"}:
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ShimoSheetError(f"人工反馈不是数字：{value!r}") from exc
    return int(number) if number.is_integer() else number


def _find_row(page: Any, config: dict[str, Any], data_date: str) -> int:
    selectors = config["selectors"]
    target = _date_value(data_date)
    # The existing sheet uses a single, contiguous daily date column. Scan a
    # bounded range so an unexpected sheet structure cannot create rows or
    # write to an arbitrary position.
    for row in range(int(config["first_daily_row"]), 401):
        actual = _date_value(_select(page, selectors, f"{config['columns']['date']}{row}"))
        if actual == target:
            return row
        if not actual:
            break
    raise ShimoSheetError(f"未找到统计日期 {data_date} 的既有行；不会自动插行")


def write_platform_metrics(report: dict[str, Any], *, config_path: Path | None = None) -> dict[str, Any]:
    """Write only platform source values, then reload and verify each value.

    Manual quality fields and pre-existing formula cells are read but never
    changed. The target date must already exist in column A.
    """
    config = _load_config(config_path)
    if not config.get("write_enabled"):
        return {"status": "disabled"}
    metrics = report.get("yesterday") or {}
    data_date = str(report["data_date"])
    values = {column: str(metrics[key]) for column, key in config["platform_write_columns"].items()
              if metrics.get(key) is not None}
    if len(values) != len(config["platform_write_columns"]):
        missing = [key for key in config["platform_write_columns"].values() if metrics.get(key) is None]
        raise ShimoSheetError(f"平台日报缺少字段，停止写入：{', '.join(missing)}")

    browser_config = load_browser_config(ROOT / "browser_download_config.json")
    record: dict[str, Any] = {"status": "started", "data_date": data_date, "written": {}, "preserved": {}}
    with _import_playwright()() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_config.profile_dir), channel="chrome", headless=browser_config.headless,
            args=[f"--profile-directory={browser_config.profile_name}"],
        )
        try:
            page = context.new_page()
            page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            row = _find_row(page, config, data_date)
            record["row"] = row
            for column in config["preserve_columns"]:
                record["preserved"][column] = _select(page, config["selectors"], f"{column}{row}")
            for column, value in values.items():
                _set_value(page, config["selectors"], f"{column}{row}", value)
                record["written"][column] = value
            # Shimo autosaves. Reload is the durable-write verification.
            page.wait_for_timeout(3500)
            page.reload(wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            if _find_row(page, config, data_date) != row:
                raise ShimoSheetError("刷新后日期行发生变化")
            for column, value in values.items():
                if _select(page, config["selectors"], f"{column}{row}") != value:
                    raise ShimoSheetError(f"刷新后平台值未保存：{column}{row}")
            for column, original in record["preserved"].items():
                if _select(page, config["selectors"], f"{column}{row}") != original:
                    raise ShimoSheetError(f"人工/公式列发生变化：{column}{row}")
            record["status"] = "success"
            return record
        finally:
            context.close()


def read_manual_quality(data_date: str, *, config_path: Path | None = None) -> dict[str, int | float | None]:
    """Read only the two human-maintained daily quality cells."""
    config = _load_config(config_path)
    browser_config = load_browser_config(ROOT / "browser_download_config.json")
    with _import_playwright()() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_config.profile_dir), channel="chrome", headless=browser_config.headless,
            args=[f"--profile-directory={browser_config.profile_name}"],
        )
        try:
            page = context.new_page()
            page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            row = _find_row(page, config, data_date)
            return {
                "valid_leads": _number_or_none(_select(page, config["selectors"], f"{config['columns']['valid_leads']}{row}")),
                "contacted_leads": _number_or_none(_select(page, config["selectors"], f"{config['columns']['contacted_leads']}{row}")),
            }
        finally:
            context.close()


def initialize_feedback_headers(*, config_path: Path | None = None) -> dict[str, Any]:
    """Create the user-approved feedback headers only in empty P2:W2 cells."""
    config = _load_config(config_path)
    headers = config["feedback_columns"]
    browser_config = load_browser_config(ROOT / "browser_download_config.json")
    with _import_playwright()() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_config.profile_dir), channel="chrome", headless=browser_config.headless,
            args=[f"--profile-directory={browser_config.profile_name}"],
        )
        try:
            page = context.new_page()
            page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            existing = {column: _select(page, config["selectors"], f"{column}{config['header_row']}") for column in headers}
            conflicts = {column: value for column, value in existing.items() if value and value != headers[column]}
            if conflicts:
                raise ShimoSheetError(f"反馈表头目标单元格已有内容：{conflicts}")
            for column, title in headers.items():
                if existing[column] != title:
                    _set_value(page, config["selectors"], f"{column}{config['header_row']}", title)
            page.wait_for_timeout(3500)
            page.reload(wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            saved = {column: _select(page, config["selectors"], f"{column}{config['header_row']}") for column in headers}
            if saved != headers:
                raise ShimoSheetError(f"反馈表头保存复核失败：{saved}")
            return {"status": "success", "row": config["header_row"], "headers": saved}
        finally:
            context.close()


def read_execution_feedback(data_date: str, *, config_path: Path | None = None) -> dict[str, str]:
    """Read the daily human execution-feedback cells without changing them."""
    config = _load_config(config_path)
    browser_config = load_browser_config(ROOT / "browser_download_config.json")
    with _import_playwright()() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_config.profile_dir), channel="chrome", headless=browser_config.headless,
            args=[f"--profile-directory={browser_config.profile_name}"],
        )
        try:
            page = context.new_page()
            page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            row = _find_row(page, config, data_date)
            column_keys = {
                "P": "recommendation_ids", "Q": "recommendation_actions",
                "R": "execution_status", "S": "actual_action", "T": "executed_at",
                "U": "result_after_1d", "V": "result_after_3d", "W": "notes",
            }
            return {
                "data_date": data_date,
                "row": row,
                **{key: _select(page, config["selectors"], f"{column}{row}")
                   for column, key in column_keys.items()},
            }
        finally:
            context.close()


def write_recommendations(data_date: str, recommendations: list[dict[str, Any]], *,
                          config_path: Path | None = None) -> dict[str, Any]:
    """Write generated suggestions to P/Q and preserve the human-owned R:W."""
    if not recommendations:
        return {"status": "skipped_no_recommendations", "data_date": data_date}
    config = _load_config(config_path)
    values = {
        "P": "\n".join(str(item["recommendation_id"]) for item in recommendations),
        "Q": "\n".join("｜".join([
            str(item.get("priority") or "P2"),
            str(item.get("action") or "观察"),
            str(item.get("object_name") or item.get("object_id") or "未命名对象"),
        ]) for item in recommendations),
    }
    browser_config = load_browser_config(ROOT / "browser_download_config.json")
    record: dict[str, Any] = {
        "status": "started", "data_date": data_date, "written": values,
        "preserved_human_feedback": {},
    }
    with _import_playwright()() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(browser_config.profile_dir), channel="chrome", headless=browser_config.headless,
            args=[f"--profile-directory={browser_config.profile_name}"],
        )
        try:
            page = context.new_page()
            page.goto(config["url"], wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            row = _find_row(page, config, data_date)
            record["row"] = row
            existing = {column: _select(page, config["selectors"], f"{column}{row}") for column in values}
            conflicts = {column: actual for column, actual in existing.items()
                         if actual and actual != values[column]}
            if conflicts:
                raise ShimoSheetError(f"建议列已有不同内容，停止覆盖：{conflicts}")
            human_columns = config.get("human_feedback_columns", ["R", "S", "T", "U", "V", "W"])
            record["preserved_human_feedback"] = {
                column: _select(page, config["selectors"], f"{column}{row}") for column in human_columns
            }
            for column, value in values.items():
                if existing[column] != value:
                    _set_value(page, config["selectors"], f"{column}{row}", value)
            page.wait_for_timeout(3500)
            page.reload(wait_until="domcontentloaded", timeout=60000)
            _ready(page, config["selectors"], config["sheet_name"])
            if _find_row(page, config, data_date) != row:
                raise ShimoSheetError("刷新后日期行发生变化")
            for column, value in values.items():
                if _select(page, config["selectors"], f"{column}{row}") != value:
                    raise ShimoSheetError(f"刷新后建议未保存：{column}{row}")
            for column, original in record["preserved_human_feedback"].items():
                if _select(page, config["selectors"], f"{column}{row}") != original:
                    raise ShimoSheetError(f"人工反馈列发生变化：{column}{row}")
            record["status"] = "success"
            return record
        finally:
            context.close()
