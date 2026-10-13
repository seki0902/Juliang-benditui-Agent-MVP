"""Restore only the exact temporary marker recorded by the authorized write check."""
import json
from verify_shimo_write import ROOT, URL, ready, select_read, load_browser_config, _import_playwright

def main():
    out = ROOT / 'output/playwright/shimo'
    path = out / 'write_verification.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    config = load_browser_config(ROOT / 'browser_download_config.json')
    with _import_playwright()() as pw:
        ctx = pw.chromium.launch_persistent_context(user_data_dir=str(config.profile_dir), channel='chrome',
                headless=False, args=[f'--profile-directory={config.profile_name}'])
        try:
            page = ctx.new_page()
            page.goto(URL, wait_until='domcontentloaded', timeout=60000)
            ready(page)
            if select_read(page) != record['marker']:
                raise RuntimeError('Marker changed; stopped without clearing')
            page.keyboard.press('Delete')
            page.wait_for_timeout(4000)
            record['after_clear'] = select_read(page)
            page.screenshot(path=str(out / 'clear_attempt.png'), full_page=True)
            page.reload(wait_until='domcontentloaded', timeout=60000)
            ready(page)
            record['restored_verified'] = select_read(page) == ''
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(record, ensure_ascii=False))
        finally:
            ctx.close()

if __name__ == '__main__':
    main()
