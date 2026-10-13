import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import browser_downloader as downloader
from run_observer import ACTIVE, RunObserver, job_lock, step, clean
from dashboard_server import history
from state_store import StateStore

class MonitoringTests(unittest.TestCase):
    def test_browser_closed_retries_but_login_does_not(self):
        with patch.object(downloader, '_download_reports_once', side_effect=[downloader.BrowserDownloadError('Target page, context or browser has been closed'), {'status':'success'}]) as run, patch.object(downloader.time, 'sleep'):
            result = downloader.download_reports(None)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(result['download_attempts'], 2)
        with patch.object(downloader, '_download_reports_once', side_effect=downloader.BrowserDownloadError('登录态失效')) as run:
            with self.assertRaises(downloader.BrowserDownloadError):
                downloader.download_reports(None)
            self.assertEqual(run.call_count, 1)

    def test_retry_is_bounded(self):
        with patch.object(downloader, '_download_reports_once', side_effect=downloader.BrowserDownloadError('Download.save_as failed')) as run, patch.object(downloader.time, 'sleep'):
            with self.assertRaises(downloader.BrowserDownloadError):
                downloader.download_reports(None)
            self.assertEqual(run.call_count, 3)

    def test_lock_prevents_concurrent_run_and_releases(self):
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp)/'lock'
            with job_lock(lock):
                with self.assertRaises(RuntimeError):
                    with job_lock(lock):
                        self.fail('concurrent lock acquired')
            with job_lock(lock):
                pass

    def test_failed_step_persists_and_attempts_remain_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            first=RunObserver(root, '2026-09-16', 'test')
            token=ACTIVE.set(first)
            try:
                @step('下载')
                def fail():
                    raise RuntimeError('closed')
                with self.assertRaises(RuntimeError):
                    fail()
                first.finish('failed', 'closed')
            finally:
                ACTIVE.reset(token)
            second=RunObserver(root, '2026-09-16', 'test')
            second.finish('success')
            self.assertNotEqual(first.path,second.path)
            self.assertEqual(json.loads(first.path.read_text(encoding='utf-8'))['steps'][0]['status'],'failed')
            self.assertEqual(len(list((root/'run_history').glob('*.json'))),2)

    def test_interrupted_process_not_displayed_as_running(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            run=RunObserver(root/'outputs','2026-09-16','test')
            with patch('dashboard_server.process_alive', return_value=False):
                self.assertEqual(history(root)[0]['status'],'interrupted')

    def test_failed_db_run_is_reset_before_retry(self):
        with tempfile.TemporaryDirectory() as temp, StateStore(Path(temp)/'state.db') as store:
            ident,_,_=store.begin_run('a:date','date','a','v3')
            store.finish_run(ident,'failed',error_summary='closed')
            store.begin_run('a:date','date','a','v3')
            row=store.connection.execute('SELECT * FROM daily_runs').fetchone()
            self.assertEqual(row['status'],'running')
            self.assertIsNone(row['finished_at'])
            self.assertIsNone(row['error_summary'])

    def test_push_secret_redacted(self):
        self.assertNotIn('SECRET',clean('https://www.pushplus.plus/send/SECRET failed'))

if __name__=='__main__':
    unittest.main()
