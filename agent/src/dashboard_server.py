"""Loopback-only operational dashboard. No credentials are served."""
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import ctypes
import json
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlparse
from run_observer import clean

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'dashboard' / 'dist'
ARTIFACTS = {'daily_report.md', 'daily_report.json', 'run_manifest.json', 'recommendations.json',
             'pending_execution_actions.json', 'shimo_sync.json', 'shimo_recommendation_sync.json', 'clawbot_push.json'}
SCHEDULE = {'status': 'checking', 'message': '正在读取 Windows 定时任务'}
RUNNER = ROOT / 'scripts' / 'run_daily.ps1'
TRIGGER_LOCK = threading.Lock()
TRIGGER_PROCESSES = {}

def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default

def process_alive(pid):
    if os.name != 'nt':
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return ctypes.get_last_error() == 5  # access denied does not prove exit
    code = ctypes.c_ulong()
    try:
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)

def poll_schedule():
    global SCHEDULE
    command = """$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8;
    try {
      $t=Get-ScheduledTask -TaskName 'Local Growth Agent Daily'; $i=$t | Get-ScheduledTaskInfo;
      [pscustomobject]@{status='available';state=[string]$t.State;next_run=$i.NextRunTime.ToString('o');
      last_run=$i.LastRunTime.ToString('o');last_result=$i.LastTaskResult;user=$t.Principal.UserId;
      logon=[string]$t.Principal.LogonType;wake=$t.Settings.WakeToRun;restart_count=$t.Settings.RestartCount;
      action=$t.Actions[0].Arguments} | ConvertTo-Json -Compress
    } catch { [pscustomobject]@{status='unavailable';message=$_.Exception.Message} | ConvertTo-Json -Compress }
    """
    while True:
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                capture_output=True, timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            SCHEDULE = json.loads(result.stdout.decode('utf-8-sig'))
        except Exception as exc:
            SCHEDULE = {'status': 'unavailable', 'message': clean(exc)}
        SCHEDULE['checked_at'] = datetime.now().astimezone().isoformat()
        time.sleep(30)

def receipts(folder):
    return {key: read_json(folder / filename, {}) for key, filename in (
        ('platform_sync', 'shimo_sync.json'), ('recommendation_sync', 'shimo_recommendation_sync.json'),
        ('push', 'clawbot_push.json'))}

def history(root=ROOT):
    results = []
    outputs = root / 'outputs'
    for path in sorted((outputs / 'run_history').glob('*.json'), reverse=True)[:100]:
        item = read_json(path)
        if not isinstance(item, dict):
            continue
        if item.get('status') == 'running' and not process_alive(item.get('pid', 0)):
            item['status'] = 'interrupted'
            item['error'] = '任务进程已退出，未记录结束状态；请查看日志后补跑'
            for stage in item.get('steps', []):
                if stage['status'] == 'running':
                    stage['status'] = 'interrupted'
        item['legacy'] = False
        results.append(item)
    # Keep historical failures separate from later successful reruns.
    for path in sorted((outputs / 'logs').glob('daily-*.log'), reverse=True)[:30]:
        try:
            raw = path.read_bytes()
            text = raw.decode('utf-16' if raw.startswith(b'\xff\xfe') else 'utf-8', errors='replace').replace('\x00', '')
        except OSError:
            continue
        match = re.search(r'daily-(\d{8})-(\d{6})', path.name)
        if not match:
            continue
        start = datetime.strptime(''.join(match.groups()), '%Y%m%d%H%M%S').astimezone()
        # New runs already have richer progress records.
        if any(abs((datetime.fromisoformat(r['started_at'])-start).total_seconds()) < 30 for r in results):
            continue
        if 'TargetClosedError' not in text and 'DailyJobError' not in text:
            continue
        error = '浏览器在保存下载文件时关闭，报表下载失败' if 'TargetClosedError' in text else '日报运行失败，请查看运行日志'
        end = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
        results.append(dict(id=path.stem, data_date=(start.date()-timedelta(days=1)).isoformat(),
            source='历史日志', status='failed', started_at=start.isoformat(), finished_at=end,
            error=error, legacy=True, steps=[dict(name='下载平台报表', status='failed', error=error)]))
    known_dates = {r.get('data_date') for r in results if r.get('status') in ('success', 'partial')}
    for folder in (outputs / 'daily_reports').glob('????-??-??'):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', folder.name) or folder.name in known_dates:
            continue
        manifest = read_json(folder / 'run_manifest.json', {})
        if manifest.get('status') not in ('success', 'partial'):
            continue
        stages = [dict(name='报表生成（已有工件）', status='success')]
        for key, label in [('platform_sync','同步石墨平台数据'),('recommendation_sync','同步石墨建议'),('push','推送日报')]:
            result = receipts(folder)[key]
            stages.append(dict(name=label, status=result.get('status', 'unknown'), error=clean(result.get('error', result.get('reason', '')))))
        status = 'success' if all(s['status'] == 'success' or s['status'].startswith('skipped') for s in stages) else 'partial'
        started = manifest.get('fetched_at') or datetime.fromtimestamp((folder/'run_manifest.json').stat().st_mtime).astimezone().isoformat()
        results.append(dict(id='report-'+folder.name, data_date=folder.name, source='历史日报', status=status,
            started_at=started, finished_at=None, legacy=True, steps=stages))
    return sorted(results, key=lambda r: r['started_at'], reverse=True)[:100]

def report_detail(data_date):
    folder = ROOT / 'outputs' / 'daily_reports' / data_date
    data = read_json(folder / 'daily_report.json', {})
    manifest = read_json(folder / 'run_manifest.json', {})
    return dict(data_date=data_date, metrics=data.get('yesterday', {}), manifest=manifest,
        receipts=receipts(folder), recommendations=read_json(folder/'recommendations.json', {}).get('recommendations', []),
        artifacts=[name for name in sorted(ARTIFACTS) if (folder/name).is_file()],
        report=(folder/'daily_report.md').read_text(encoding='utf-8') if (folder/'daily_report.md').is_file() else '')

def _running_history_item():
    return next((item for item in history() if item.get('status') == 'running'), None)

def _running_trigger_process():
    for pid, process in list(TRIGGER_PROCESSES.items()):
        if process.poll() is not None:
            TRIGGER_PROCESSES.pop(pid, None)
            continue
        return process
    return None

def trigger_daily():
    """Start the same PowerShell entrypoint used by the daily task.

    The dashboard only launches the process. Progress and errors are persisted
    by the existing RunObserver, so the normal dashboard refresh can display
    the run without duplicating pipeline logic here.
    """
    with TRIGGER_LOCK:
        running = _running_history_item()
        if running:
            return {
                'status': 'running',
                'message': '已有 Agent 任务正在运行，请等待它完成。',
                'run': running,
            }, 409
        launched = _running_trigger_process()
        if launched:
            return {
                'status': 'running',
                'message': 'Agent 正在启动，请等待运行记录出现。',
                'pid': launched.pid,
            }, 409
        if not RUNNER.is_file():
            return {'status': 'unavailable', 'message': f'找不到运行入口：{RUNNER.name}'}, 503
        browser_config = ROOT / 'browser_download_config.json'
        if not browser_config.is_file():
            return {
                'status': 'unavailable',
                'message': '还没有找到浏览器下载配置，请先完成 browser_download_config.json 配置。',
            }, 503

        logs = ROOT / 'outputs' / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        launch_log = logs / 'dashboard-trigger.log'
        command = [
            'powershell.exe', '-NoProfile', '-WindowStyle', 'Hidden',
            '-ExecutionPolicy', 'Bypass', '-File', str(RUNNER),
            '-RunSource', 'manual',
        ]
        env = os.environ.copy()
        env['AGENT_RUN_SOURCE'] = 'manual'
        try:
            with launch_log.open('ab') as output:
                process = subprocess.Popen(
                    command,
                    cwd=str(ROOT),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
        except OSError as exc:
            return {'status': 'unavailable', 'message': f'无法启动 Agent：{clean(exc)}'}, 503
        TRIGGER_PROCESSES[process.pid] = process
        return {
            'status': 'starting',
            'message': 'Agent 已启动，正在下载报表并生成日报。',
            'pid': process.pid,
            'started_at': datetime.now().astimezone().isoformat(),
        }, 202

class Handler(BaseHTTPRequestHandler):
    def send(self, data, content_type='application/json; charset=utf-8', status=200):
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        elif isinstance(data, str):
            data = data.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # Prevent DNS rebinding; do not expose this service on LAN interfaces.
        if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            return self.send({'error': 'Invalid host'}, status=403)
        path = urlparse(self.path).path
        try:
            if path == '/api/status':
                runs = history()
                return self.send(dict(runs=runs, schedule=SCHEDULE, project=str(ROOT),
                    expected_date=(datetime.now().date()-timedelta(days=1)).isoformat(),
                    server_time=datetime.now().astimezone().isoformat()))
            match = re.fullmatch(r'/api/report/(\d{4}-\d{2}-\d{2})', path)
            if match:
                return self.send(report_detail(match[1]))
            match = re.fullmatch(r'/artifacts/(\d{4}-\d{2}-\d{2})/([a-z_]+\.(?:md|json))', path)
            if match and match[2] in ARTIFACTS:
                target = ROOT/'outputs'/'daily_reports'/match[1]/match[2]
                if target.is_file():
                    return self.send(target.read_bytes(), 'text/plain; charset=utf-8')
            allowed = {'/': ('index.html','text/html'), '/app.js':('app.js','text/javascript'), '/style.css':('style.css','text/css')}
            if path in allowed:
                name, mime = allowed[path]
                return self.send((STATIC/name).read_bytes(), mime+'; charset=utf-8')
            return self.send({'error': 'Not found'}, status=404)
        except Exception as exc:
            self.send({'error': clean(exc)}, status=500)

    def do_POST(self):
        # Prevent DNS rebinding; do not expose this service on LAN interfaces.
        if self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            return self.send({'error': 'Invalid host'}, status=403)
        path = urlparse(self.path).path
        if path != '/api/run':
            return self.send({'error': 'Not found'}, status=404)
        try:
            data, status = trigger_daily()
            return self.send(data, status=status)
        except Exception as exc:
            return self.send({'error': clean(exc)}, status=500)

    def log_message(self, *_):
        pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8898)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    threading.Thread(target=poll_schedule, daemon=True).start()
    print(f'Agent dashboard: http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()

if __name__ == '__main__':
    main()
