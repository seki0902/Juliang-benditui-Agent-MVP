"""Validate P1 configuration without printing secrets or making network calls."""

from __future__ import annotations

import argparse

try:  # Support both PYTHONPATH=agent/src and package-style imports.
    from .config import Settings
except ImportError:  # pragma: no cover - exercised when run as a file.
    from config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="P1 read-only configuration check")
    parser.add_argument("--refresh", action="store_true", help="also check token-refresh configuration")
    args = parser.parse_args()

    settings = Settings.from_env()
    missing = settings.missing_for_project_probe()
    print("P1 配置检查")
    print(f"base_url={settings.oceanengine_base_url}")
    print(f"project_list_path={settings.oceanengine_project_list_path}")
    print(f"local_account_id_present={bool(settings.oceanengine_local_account_id)}")
    print(f"access_token_present={bool(settings.oceanengine_access_token)}")
    print(f"refresh_token_present={bool(settings.oceanengine_refresh_token)}")
    print(f"feedback_export={settings.shimo_feedback_export or '未配置'}")
    print(f"project_probe_missing={','.join(missing) if missing else 'none'}")
    if args.refresh:
        refresh_missing = settings.missing_for_token_refresh()
        print(f"token_refresh_missing={','.join(refresh_missing) if refresh_missing else 'none'}")


if __name__ == "__main__":
    main()
