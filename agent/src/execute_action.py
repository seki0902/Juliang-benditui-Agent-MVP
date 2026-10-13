"""Separate read-only preparation, explicit human approval, and execution."""
import argparse
import json
from pathlib import Path

from browser_executor import BrowserExecutor, ROOT
from execution_controller import ExecutionController


def load_verified_candidate(path: Path, candidate_id: str) -> dict:
    """Select a candidate from its immutable daily artifact, fail closed otherwise."""
    document = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(document, dict) or not isinstance(document.get('actions'), list):
        raise ValueError('候选必须来自 daily_job 写入的 pending_execution_actions.json，不能使用手工单条 JSON')
    report_path = path.parent / 'daily_report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if document.get('run_key') != f"{report.get('account_id')}:{report.get('data_date')}":
        raise ValueError('候选文件与同目录正式日报的运行标识不匹配')
    if report.get('reconciliation', {}).get('all_pass') is not True:
        raise ValueError('同目录正式日报未通过对账，不能准备执行动作')
    matches = [item for item in document['actions'] if item.get('action_id') == candidate_id]
    if len(matches) != 1:
        raise ValueError('候选动作 ID 不存在或不唯一')
    candidate = matches[0]
    if (candidate.get('account_id') != report.get('account_id')
            or candidate.get('data_date') != report.get('data_date')
            or candidate.get('reconciliation_pass') is not True):
        raise ValueError('候选与正式日报身份或对账状态不匹配')
    return candidate


def main():
    parser = argparse.ArgumentParser(description='投放动作审批执行入口')
    parser.add_argument('--db', type=Path, default=ROOT / 'state' / 'execution.sqlite3')
    parser.add_argument('--browser-config', type=Path, default=ROOT / 'browser_execution_config.json')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--candidate', type=Path, required=True)
    prepare.add_argument('--candidate-id', required=True)
    prepare.add_argument('--target', required=True)
    approve = commands.add_parser('approve')
    approve.add_argument('--id', required=True)
    approve.add_argument('--digest', required=True)
    approve.add_argument('--confirmation', required=True)
    execute = commands.add_parser('execute')
    execute.add_argument('--id', required=True)
    show = commands.add_parser('show')
    show.add_argument('--id', required=True)
    args = parser.parse_args()
    controller = ExecutionController(args.db)
    try:
        if args.command == 'prepare':
            candidate = load_verified_candidate(args.candidate, args.candidate_id)
            with BrowserExecutor(args.browser_config, read_only=True) as adapter:
                result = controller.prepare(candidate, adapter, args.target)
        elif args.command == 'approve':
            controller.approve(args.id, args.digest, args.confirmation)
            result = {'id': args.id, 'state': 'approved'}
        elif args.command == 'execute':
            with BrowserExecutor(args.browser_config) as adapter:
                result = controller.execute(args.id, adapter)
        else:
            result = dict(controller.get(args.id))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        controller.close()


if __name__ == '__main__':
    main()
