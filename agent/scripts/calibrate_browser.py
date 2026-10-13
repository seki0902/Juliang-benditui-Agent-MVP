"""Read the real report page using the isolated daily-download profile."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from browser_downloader import load_browser_config, _prepare_profile, _import_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description="独立 Chrome 目录只读页面校准")
    parser.add_argument("--config", type=Path, default=ROOT / "browser_download_config.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output/playwright")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    config = load_browser_config(args.config)
    _prepare_profile(config)
    args.output.mkdir(parents=True, exist_ok=True)
    with _import_playwright()() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.profile_dir), channel=config.browser_channel,
            headless=config.headless, accept_downloads=True, timeout=config.timeout_ms,
            args=[f"--profile-directory={config.profile_name}"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.report_url, wait_until="domcontentloaded", timeout=config.timeout_ms)
            page.locator("body").wait_for(state="visible", timeout=config.timeout_ms)
            snapshot = page.locator("body").aria_snapshot(timeout=config.timeout_ms)
            (args.output / "chrome_calibration.txt").write_text(snapshot, encoding="utf-8")
            page.screenshot(path=str(args.output / "chrome_calibration.png"), full_page=True)
            print(json.dumps({"url": page.url, "title": page.title(),
                              "snapshot": snapshot, "profile": str(config.profile_dir)}, ensure_ascii=False), flush=True)
            while args.interactive:
                try:
                    command = json.loads(input())
                except EOFError:
                    break
                if command["action"] == "quit":
                    break
                try:
                    if command["action"] in {"click", "hover", "fill"}:
                        locator = page.locator(command["selector"])
                        if command["action"] == "fill":
                            locator.fill(command["value"], timeout=15000)
                        else:
                            getattr(locator, command["action"])(timeout=15000)
                    elif command["action"] == "press":
                        page.locator(command["selector"]).press(command["key"])
                    elif command["action"] == "inspect":
                        details = page.locator(command["selector"]).evaluate_all("els => els.map(el => el.outerHTML)")
                        (args.output / "controls.json").write_text(json.dumps(details, ensure_ascii=False), encoding="utf-8")
                    elif command["action"] == "scroll":
                        page.locator(command["selector"]).scroll_into_view_if_needed()
                    snapshot = page.locator("body").aria_snapshot()
                    (args.output / "chrome_calibration.txt").write_text(snapshot, encoding="utf-8")
                    print(json.dumps({"url": page.url, "status": "observed"}, ensure_ascii=True), flush=True)
                    page.screenshot(path=str(args.output / "chrome_calibration.png"), full_page=True)
                except Exception as exc:
                    print(json.dumps({"error": str(exc)}, ensure_ascii=True), flush=True)
        finally:
            context.close()


if __name__ == "__main__":
    main()
