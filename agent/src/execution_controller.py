"""Durable, one-shot approvals for ad changes. No scheduled auto-approval."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class ExecutionBlocked(RuntimeError):
    pass


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def money(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0 or number != number.quantize(Decimal('0.01')):
        raise ExecutionBlocked('预算必须为正数且最多两位小数')
    return number


def normalized_money(value: Any) -> str:
    """Return the only money representation allowed in approved payloads."""
    return format(money(value), '.2f')


def _policy() -> dict[str, Any]:
    """Load the local execution policy; a candidate must never loosen it."""
    try:
        value = json.loads((ROOT / 'execution_policy.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionBlocked(f'无法读取执行策略: {exc}') from exc
    if value.get('execution_mode') != 'confirmation_required':
        raise ExecutionBlocked('执行策略不是逐条人工确认模式')
    if value.get('all_operations_require_human_confirmation') is not True:
        raise ExecutionBlocked('执行策略未强制人工确认')
    if value.get('on_page_mismatch') != 'stop_without_change':
        raise ExecutionBlocked('执行策略未要求页面不一致时停止')
    try:
        maximum = Decimal(str(value['max_budget_change_ratio']))
    except (KeyError, ArithmeticError, ValueError) as exc:
        raise ExecutionBlocked('执行策略缺少有效的预算变动上限') from exc
    if not Decimal('0') < maximum <= Decimal('0.10'):
        raise ExecutionBlocked('执行策略预算变动上限必须在 0 到 10% 之间')
    pause = value.get('pause_rule') or {}
    anomaly = value.get('single_day_anomaly') or {}
    if (pause.get('minimum_consecutive_spend_days') != 3
            or pause.get('maximum_platform_leads') != 0
            or pause.get('requires_human_confirmation') is not True
            or anomaly.get('requires_human_confirmation') is not True
            or set(anomaly.get('allowed_operations') or []) != {'decrease_budget'}):
        raise ExecutionBlocked('执行策略的暂停或单日异常限制不符合要求')
    return value


def pause_evidence(rows: list[dict], data_date: str) -> bool:
    required = {(date.fromisoformat(data_date) - timedelta(days=i)).isoformat() for i in range(3)}
    selected = [r for r in rows if r.get('data_date') in required]
    if len(selected) != 3 or {r['data_date'] for r in selected} != required:
        return False
    try:
        return all(Decimal(str(r['spend'])).is_finite() and Decimal(str(r['spend'])) > 0
                   and Decimal(str(r['leads'])) == 0 for r in selected)
    except (KeyError, ArithmeticError, ValueError):
        return False


def validate(proposal: dict) -> None:
    if not all(proposal.get(k) for k in ('account_id', 'object_id', 'object_name', 'data_date')):
        raise ExecutionBlocked('缺少账户、计划身份或统计日期')
    if proposal.get('reconciliation_pass') is not True:
        raise ExecutionBlocked('数据未通过对账')
    policy = _policy()
    candidate_policy = proposal.get('policy') or {}
    if candidate_policy.get('requires_human_confirmation') is not True:
        raise ExecutionBlocked('候选未标明必须人工确认')
    operation = proposal['operation']
    before = proposal['before']
    if any(str(before.get(key)) != str(proposal[key]) for key in ('account_id', 'object_id', 'object_name')):
        raise ExecutionBlocked('实时快照与审批对象不一致')
    if before['status'] != 'enabled':
        raise ExecutionBlocked('仅允许修改当前启用的计划')
    if operation == 'pause_plan':
        if not pause_evidence(proposal.get('daily_evidence', []), proposal['data_date']):
            raise ExecutionBlocked('暂停必须有连续三个自然日每天消耗>0且每天留资=0的完整证据')
        if proposal['target'] != 'paused':
            raise ExecutionBlocked('暂停目标状态无效')
    elif operation in ('decrease_budget', 'increase_budget'):
        current, target = money(before['budget']), money(proposal['target'])
        change = (target-current)/current
        if abs(change) > Decimal(str(policy['max_budget_change_ratio'])) or change == 0:
            raise ExecutionBlocked('单次预算变动必须大于0且不超过10%')
        if (operation == 'decrease_budget') != (change < 0):
            raise ExecutionBlocked('预算方向与动作不一致')
        if proposal.get('single_day_anomaly', True) and change > 0:
            raise ExecutionBlocked('单日异常只能降预算')
    else:
        raise ExecutionBlocked('未授权的动作类型；开启、出价和素材操作不可执行')


class ExecutionController:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS execution_actions (
                id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, expires_at TEXT NOT NULL, approval TEXT);
            CREATE TABLE IF NOT EXISTS execution_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL);
        ''')
        # These columns support a per-account/per-plan active-action lock.  Keep
        # the JSON payload as the complete audit record, while indexing identity
        # independently of SQLite JSON extension availability.
        columns = {row['name'] for row in self.db.execute('PRAGMA table_info(execution_actions)')}
        if 'account_id' not in columns:
            self.db.execute('ALTER TABLE execution_actions ADD COLUMN account_id TEXT')
        if 'object_id' not in columns:
            self.db.execute('ALTER TABLE execution_actions ADD COLUMN object_id TEXT')
        self.db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS execution_active_plan_lock
                           ON execution_actions(account_id, object_id)
                           WHERE state IN ('approved', 'executing')''')
        self.db.commit()

    def audit(self, action_id: str, event: str, detail: dict) -> None:
        self.db.execute('INSERT INTO execution_audit(action_id,recorded_at,event,detail) VALUES (?,?,?,?)',
                        (action_id, datetime.now(timezone.utc).isoformat(), event, canonical(detail)))

    def prepare(self, candidate: dict, adapter: Any, target: str) -> dict:
        # Read live values BEFORE asking for approval; approval binds exact money.
        execution_target = candidate.get('execution_target')
        if not isinstance(execution_target, dict):
            raise ExecutionBlocked('候选没有正式的单元到项目执行映射')
        if execution_target.get('object_type') != 'project' or not all(
                execution_target.get(key) for key in ('object_id', 'object_name', 'mapping_source')):
            raise ExecutionBlocked('候选项目执行映射不完整或不可追溯')
        source_object = {key: candidate.get(key) for key in ('object_type', 'object_id', 'object_name')}
        live_candidate = {**candidate, 'object_type': 'project',
                          'object_id': str(execution_target['object_id']),
                          'object_name': str(execution_target['object_name'])}
        mapping = adapter.verify_mapping(candidate)
        before = adapter.read(live_candidate)
        proposal = {**live_candidate, 'source_object': source_object, 'live_mapping': mapping, 'before': before,
                    'target': 'paused' if target == 'paused' else normalized_money(target)}
        validate(proposal)
        payload = canonical(proposal)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        action_id = 'act_' + uuid.uuid4().hex
        expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        with self.db:
            # Expiration never clears executing/unknown: those require review.
            self.db.execute("UPDATE execution_actions SET state='expired' WHERE state IN ('pending','approved') AND expires_at<=?",
                            (datetime.now(timezone.utc).isoformat(),))
            unresolved = self.db.execute(
                "SELECT id FROM execution_actions WHERE account_id=? AND object_id=? AND state IN ('executing','unknown')",
                (proposal['account_id'], proposal['object_id'])).fetchone()
            if unresolved:
                raise ExecutionBlocked('此项目存在执行中或结果未知的动作，需先人工核查')
            existing = self.db.execute('SELECT * FROM execution_actions WHERE digest=?', (digest,)).fetchone()
            if existing:
                if existing['state'] in ('pending', 'approved', 'succeeded'):
                    return dict(existing)
                if existing['state'] not in ('expired', 'blocked'):
                    raise ExecutionBlocked('旧动作不可自动重试')
                # A renewed approval is a new payload/digest; preserve old audit.
                proposal = {**proposal, 'approval_version': action_id,
                            'supersedes': existing['id']}
                payload = canonical(proposal)
                digest = hashlib.sha256(payload.encode()).hexdigest()
            self.db.execute('''INSERT INTO execution_actions
                               (id,digest,payload,state,expires_at,approval,account_id,object_id)
                               VALUES (?,?,?,?,?,?,?,?)''',
                            (action_id, digest, payload, 'pending', expires, None,
                             proposal['account_id'], proposal['object_id']))
            self.audit(action_id, 'prepared', proposal)
        return {'id': action_id, 'digest': digest, 'proposal': proposal, 'state': 'pending', 'expires_at': expires}

    def get(self, action_id: str):
        row = self.db.execute('SELECT * FROM execution_actions WHERE id=?', (action_id,)).fetchone()
        if row is None:
            raise ExecutionBlocked('动作不存在')
        return row

    def approve(self, action_id: str, digest: str, confirmation: str) -> None:
        expected = f'确认执行 {action_id} {digest}'
        if confirmation != expected:
            raise ExecutionBlocked('需用户针对动作ID和完整摘要指纹明确确认')
        with self.db:
            try:
                cursor = self.db.execute(
                    "UPDATE execution_actions SET state='approved', approval=? WHERE id=? AND digest=? AND state='pending' AND expires_at>?",
                    (confirmation, action_id, digest, datetime.now(timezone.utc).isoformat()))
            except sqlite3.IntegrityError as exc:
                raise ExecutionBlocked('该账户与计划已有获批或执行中的动作，必须先完成人工复核') from exc
            if cursor.rowcount != 1:
                raise ExecutionBlocked('动作已过期、已处理或审批内容不匹配')
            self.audit(action_id, 'approved', {'confirmation': confirmation})

    def execute(self, action_id: str, adapter: Any) -> dict:
        row = self.get(action_id)
        if row['state'] != 'approved' or row['expires_at'] <= datetime.now(timezone.utc).isoformat():
            raise ExecutionBlocked('动作未确认或已过期')
        proposal = json.loads(row['payload'])
        if hashlib.sha256(canonical(proposal).encode()).hexdigest() != row['digest']:
            raise ExecutionBlocked('审批内容指纹不一致')
        validate(proposal)
        # Atomic claim prevents simultaneous processes/retries from repeating a change.
        with self.db:
            cursor = self.db.execute("UPDATE execution_actions SET state='executing' WHERE id=? AND state='approved' AND expires_at>?", (action_id, datetime.now(timezone.utc).isoformat()))
            if cursor.rowcount != 1:
                raise ExecutionBlocked('动作已被其他执行进程领取')
            self.audit(action_id, 'claimed', {})
        submitted = False
        try:
            adapter.verify_mapping(proposal)
            before = adapter.read(proposal)
            if before != proposal['before']:
                raise ExecutionBlocked('后台值已变化，原确认失效，需要重新生成动作')
            adapter.capture(action_id, 'before')
            # Mark unknown before the first possible mutation. Never blindly retry.
            with self.db:
                self.audit(action_id, 'submission_started', {})
            submitted = True
            adapter.apply(proposal)
            after = adapter.read(proposal, refresh=True)
            adapter.capture(action_id, 'after')
            expected = {**before, ('status' if proposal['operation'] == 'pause_plan' else 'budget'): proposal['target']}
            if after != expected:
                raise ExecutionBlocked('提交后读回值与批准目标不一致，需人工核查')
            state, detail = 'succeeded', {'before': before, 'after': after}
        except Exception as exc:
            state = 'unknown' if submitted else 'blocked'
            detail = {'error': str(exc), 'requires_human_review': True}
        with self.db:
            self.db.execute('UPDATE execution_actions SET state=? WHERE id=?', (state, action_id))
            self.audit(action_id, state, detail)
        return {'id': action_id, 'state': state, **detail}

    def close(self):
        self.db.close()
