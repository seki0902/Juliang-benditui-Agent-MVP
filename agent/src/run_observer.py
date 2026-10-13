"""Durable per-attempt progress and process-wide exclusion for daily jobs."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from pathlib import Path
import json
import os
import re
import uuid

ACTIVE = ContextVar('active_run', default=None)

def now():
    return datetime.now().astimezone().isoformat()

def clean(value):
    text = str(value)
    text = re.sub(r'https://www\.pushplus\.plus/send/[^\s\"\']+', 'https://www.pushplus.plus/send/[redacted]', text)
    return text[:4000]

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)

@contextmanager
def job_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('已有日报任务运行中，请等待完成；未启动重复任务') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

class RunObserver:
    def __init__(self, root, data_date, source):
        ident = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        self.path = root / 'run_history' / (ident + '.json')
        self.data = dict(id=ident, data_date=data_date, source=source, pid=os.getpid(),
                         status='running', started_at=now(), finished_at=None, steps=[])
        self.save()

    def save(self):
        self.data['updated_at'] = now()
        atomic_json(self.path, self.data)

    def finish(self, status, error=None):
        self.data.update(status=status, finished_at=now(), error=clean(error) if error else None)
        self.save()

def step(label):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            observer = ACTIVE.get()
            if observer is None:
                return fn(*args, **kwargs)
            item = dict(name=label, status='running', started_at=now())
            observer.data['steps'].append(item)
            observer.save()
            try:
                result = fn(*args, **kwargs)
                status = result.get('status', 'success') if isinstance(result, dict) else 'success'
                item['status'] = status
                if isinstance(result, dict):
                    for key in ('error', 'reason', 'message_id', 'data_date'):
                        if result.get(key) is not None:
                            item[key] = clean(result[key])
                return result
            except Exception as exc:
                item.update(status='failed', error=clean(exc))
                raise
            finally:
                item['finished_at'] = now()
                observer.save()
        return wrapped
    return decorate

def observed_job(fn):
    @wraps(fn)
    def wrapped(**kwargs):
        project = Path(__file__).resolve().parents[1]
        outputs = kwargs.get('output_root') or project / 'outputs' / 'daily_reports'
        from browser_downloader import load_browser_config, resolve_date_range
        date = None
        if kwargs.get('browser_config_path'):
            config = load_browser_config(kwargs['browser_config_path'])
            date = resolve_date_range(config.date_mode, timezone=config.timezone)[1].isoformat()
        with job_lock(project / 'state' / 'daily.lock'):
            observer = RunObserver(outputs.parent, date, os.getenv('AGENT_RUN_SOURCE', 'manual'))
            token = ACTIVE.set(observer)
            try:
                result = fn(**kwargs)
                observer.data['data_date'] = result.get('data_date', date)
                status = result.get('status', 'success')
                if status == 'success' and any(s['status'] in ('failed', 'unavailable', 'disabled') for s in observer.data['steps']):
                    status = 'partial'
                observer.finish(status)
                return result
            except Exception as exc:
                observer.finish('failed', exc)
                raise
            finally:
                ACTIVE.reset(token)
    return wrapped
