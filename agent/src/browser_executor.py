"""Explicitly calibrated Playwright adapter; absent selectors fail closed."""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from browser_downloader import load_browser_config, _import_playwright
from execution_controller import ExecutionBlocked, money

ROOT = Path(__file__).resolve().parents[1]


def read_single_child(page, account_id: str, project_id: str) -> dict:
    """Read the observed project drill-down page; never infer IDs from names."""
    if not account_id.isdigit() or not project_id.isdigit():
        raise ExecutionBlocked('账户和项目ID必须为数字')
    page.goto('https://localads.chengzijianzhan.cn/lamp/pc/cdp_promotion/promote-manage/project/ads'
              f'?advid={account_id}&local_project_id={project_id}&projectIndex=0&landingType=1',
              wait_until='domcontentloaded')
    page.get_by_text(f'项目ID：{project_id}', exact=True).wait_for(timeout=30000)
    page.get_by_text('共 1 条记录', exact=True).wait_for(timeout=15000)
    url = urlparse(page.url)
    query = parse_qs(url.query)
    if (url.hostname != 'localads.chengzijianzhan.cn'
            or query.get('advid') != [account_id]
            or query.get('local_project_id') != [project_id]):
        raise ExecutionBlocked('项目详情页身份不匹配')
    page.get_by_text(f'ID：{account_id}', exact=True).wait_for(timeout=10000)
    ids = []
    for row in page.locator('tr.ovui-tr').all():
        matches = re.findall(r'(?m)^ID: (\d+)\s*$', row.inner_text())
        ids.extend(matches)
    if len(ids) != 1:
        raise ExecutionBlocked('项目下不是唯一单元，需项目级汇总证据')
    return {'project_id': project_id, 'unit_id': ids[0], 'child_count': 1,
            'source': 'live_project_detail', 'url': page.url}


class BrowserInspector:
    """Visible, read-only page observer used before selector calibration."""

    def __init__(self, url: str):
        self.url = url
        self.context = self.playwright = self.page = None

    def __enter__(self):
        config = load_browser_config(ROOT / 'browser_download_config.json')
        self.playwright = _import_playwright()().start()
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(config.profile_dir), channel=config.browser_channel, headless=False,
                args=[f'--profile-directory={config.profile_name}'])
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.goto(self.url, wait_until='domcontentloaded')
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.playwright:
                self.playwright.stop()

    def observe(self, plan_id: str | None = None) -> dict:
        from datetime import datetime, timezone

        # Navigation returns before this SPA finishes its account/list queries.
        # This is a passive wait: no filters, pagination, or delivery controls
        # are touched during calibration.
        self.page.wait_for_timeout(15000)
        directory = ROOT / 'output' / 'playwright' / 'inspection'
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        screenshot = directory / f'execution-page-{stamp}.png'
        self.page.screenshot(path=str(screenshot), full_page=True)
        body = self.page.locator('body').inner_text(timeout=15000)
        classes = self.page.locator('[class]').evaluate_all(
            "nodes => [...new Set(nodes.map(n => n.className).filter(v => typeof v === 'string' && /table|list|row|cell/i.test(v)))].slice(0, 100)"
        )
        result = {'url': self.page.url, 'title': self.page.title(),
                'screenshot': str(screenshot), 'body_excerpt': body[:6000],
                'visible_loading_count': self.page.get_by_text('加载中...').count(),
                'table_related_classes': classes}
        if plan_id:
            # This serialises only the ancestry of an ID already observed on
            # screen.  It does not search, click, or change the table state.
            result['plan_id_dom_evidence'] = self.page.evaluate('''planId => {
                const wanted = `ID: ${planId}`;
                const node = [...document.querySelectorAll('*')].find(item => item.textContent.trim() === wanted);
                if (!node) return null;
                const chain = [];
                for (let current = node; current && chain.length < 8; current = current.parentElement) {
                  chain.push({tag: current.tagName, className: current.className || '',
                    dataAttributes: Object.fromEntries([...current.attributes]
                      .filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])),
                    html: current.outerHTML.slice(0, 6000)});
                }
                const row = node.closest('tr');
                return {chain, cells: row ? [...row.querySelectorAll(':scope > td')].map((cell, index) => ({
                  index, text: cell.innerText.trim(), className: cell.className,
                  dataAttributes: Object.fromEntries([...cell.attributes]
                    .filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value])),
                  html: index === 5 ? cell.innerHTML : undefined
                })) : []};
            }''', plan_id)
        return result


class BrowserExecutor:
    def __init__(self, config_path: Path, *, read_only: bool = False):
        self.read_only = read_only
        self.config = json.loads(config_path.read_text(encoding='utf-8'))
        if self.config.get('read_calibrated' if read_only else 'calibrated') is not True:
            raise ExecutionBlocked('执行页面定位尚未完成实页校准，不能启动写操作')
        self.selectors = self.config['selectors']
        for name in ('account_id', 'plan_row', 'plan_id', 'plan_name', 'budget', 'status'):
            if not self.selectors.get(name):
                raise ExecutionBlocked(f'缺少已校准定位：{name}')
        self.context = self.playwright = None

    def __enter__(self):
        config = load_browser_config(ROOT / 'browser_download_config.json')
        self.playwright = _import_playwright()().start()
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(config.profile_dir), channel=config.browser_channel, headless=False,
                args=[f'--profile-directory={config.profile_name}'])
            self.page = self.context.new_page()
            self.page.goto(self.config['url'], wait_until='domcontentloaded')
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.playwright:
                self.playwright.stop()

    def one(self, parent, name):
        locator = parent.locator(self.selectors[name])
        locator.first.wait_for(state='visible', timeout=15000)
        if locator.count() != 1:
            raise ExecutionBlocked(f'页面定位不唯一：{name}')
        return locator

    def read(self, proposal: dict, refresh=False):
        if proposal.get('object_type') != 'project':
            raise ExecutionBlocked('项目页面不能接受单元ID作为执行对象')
        if refresh:
            self.page.reload(wait_until='domcontentloaded')
        url = urlparse(self.page.url)
        expected_url = urlparse(self.config['url'])
        if url.hostname != expected_url.hostname or url.path != expected_url.path:
            raise ExecutionBlocked('页面跳转或需要人工登录')
        account_id = str(proposal['account_id'])
        if parse_qs(url.query).get('advid') != [account_id]:
            raise ExecutionBlocked('页面账户参数不匹配')
        if self.one(self.page, 'account_id').inner_text().strip() != 'ID：' + account_id:
            raise ExecutionBlocked('页面实际账户不匹配')
        rows = self.page.locator(self.selectors['plan_row'])
        self.page.locator(self.selectors['plan_id']).filter(has_text='ID: ' + str(proposal['object_id'])).first.wait_for(timeout=15000)
        if self.page.locator('th').nth(5).inner_text().strip() != '项目预算':
            raise ExecutionBlocked('项目预算列顺序变化')
        matches = []
        for index in range(rows.count()):
            row = rows.nth(index)
            identity = row.locator(self.selectors['plan_id'])
            if identity.count() == 1 and identity.inner_text().strip() == 'ID: ' + str(proposal['object_id']):
                matches.append(row)
        if len(matches) != 1:
            raise ExecutionBlocked('当前列表没有唯一匹配的计划ID；请先定位计划')
        self.row = matches[0]
        name = self.one(self.row, 'plan_name').inner_text().strip()
        if name != proposal['object_name']:
            raise ExecutionBlocked('计划名称与ID不匹配')
        raw_status = self.one(self.row, 'status').inner_text().strip()
        status = self.config['status_values'].get(raw_status)
        if status not in ('enabled', 'paused'):
            raise ExecutionBlocked('无法识别实时状态')
        raw_budget = self.one(self.row, 'budget').inner_text().strip().replace(',', '')
        budget = format(money(raw_budget), '.2f')
        return {'account_id': account_id, 'object_id': str(proposal['object_id']),
                'object_name': name, 'budget': budget, 'status': status}

    def verify_mapping(self, candidate: dict) -> dict:
        target = candidate['execution_target']
        mapping = read_single_child(self.page, str(candidate['account_id']), str(target['object_id']))
        source = candidate.get('source_object') or candidate
        if mapping['unit_id'] != str(source['object_id']):
            raise ExecutionBlocked('项目唯一子单元与建议来源不匹配')
        self.page.goto(self.config['url'], wait_until='domcontentloaded')
        return mapping

    def capture(self, action_id: str, phase: str):
        directory = ROOT / 'output' / 'playwright' / 'execution'
        directory.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(directory / f'{action_id}-{phase}.png'), full_page=True)

    def apply(self, proposal: dict):
        if self.read_only or self.config.get('calibrated') is not True:
            raise ExecutionBlocked('当前浏览器只允许读取，写操作尚未校准')
        # Submission behavior and paused-state mapping have not been observed.
        raise ExecutionBlocked('提交交互尚未校准，禁止真实写操作')
