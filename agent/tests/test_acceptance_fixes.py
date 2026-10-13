from pathlib import Path
import json
import tempfile
from unittest.mock import patch
import pytest
from config import Settings
from services.pipeline_switch import DailyPipelineRequest
from services.report_renderer import render_daily_report


def test_enabled_daily_job_wires_default_runner():
    import daily_job
    with patch('services.daily_agent_runner.run_daily_agent', return_value={'status':'pass'}) as runner:
        result = daily_job.run_daily.__wrapped__(settings=Settings(agent_enabled=True), input_dir=Path('account'))
    assert result['pipeline_mode'] == 'multi_agent'
    assert runner.call_args.args[0].input_dir == Path('account')


def test_missing_credentials_returns_blocked_without_loading_inputs():
    from services.daily_agent_runner import run_daily_agent
    result = run_daily_agent(DailyPipelineRequest(input_dir=Path('absent')), settings=Settings(agent_enabled=True, agent_model='fixture'))
    assert result['status'] == 'blocked'
    assert 'DEEPSEEK_API_KEY' in result['missing']
    assert result['push'] == 'disabled'


def test_missing_input_never_downloads_or_calls_model():
    from services.daily_agent_runner import run_daily_agent
    result = run_daily_agent(DailyPipelineRequest(), settings=Settings(agent_enabled=True), client=object())
    assert result['status'] == 'blocked'
    assert '--input-dir' in result['missing']


def test_output_inside_inputs_is_rejected():
    from services.daily_agent_runner import run_daily_agent
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError, match='input'):
            run_daily_agent(DailyPipelineRequest(input_dir=Path(tmp), output_root=Path(tmp)/'output'), settings=Settings(agent_enabled=True), client=object())


def test_overview_preserves_partial_coverage_and_zero():
    report = render_daily_report({'strategy_result': {'overall_judgement':'账户消耗集中。深转（石墨）全窗口缺失，无法评价。'}, 'account_context': {'data_status': {'deep_conversion_coverage': {'3d': {'available_days':['2026-09-14','2026-09-15'], 'missing_dates':['2026-09-16'], 'blank_value_dates':{'valid_leads':['2026-09-15']}, 'valid_leads':None}}}}})
    assert '全窗口缺失' not in report
    assert '账户消耗集中' in report
    assert '2026-09-14' in report and '2026-09-15' in report and '2026-09-16' in report
    assert 'valid_leads' in report
    zero = render_daily_report({'account_context': {'data_status': {'deep_conversion_coverage': {'1d': {'available_days':['2026-09-14'], 'missing_dates':[], 'blank_value_dates':{}, 'valid_leads':0}}}}})
    assert '有效深转合计=0' in zero


def test_no_records_does_not_claim_partial_data():
    report = render_daily_report({'account_context': {'data_status': {'deep_conversion_coverage': {'1d': {'available_days':[], 'missing_dates':['2026-09-14'], 'blank_value_dates':{}, 'valid_leads':None}}}}})
    assert '窗口无记录' in report
    assert '有效深转合计=0' not in report


def test_overview_does_not_remove_unrelated_missing_data_claim():
    report=render_daily_report({'strategy_result': {'overall_judgement':'素材正文全部缺失。'}, 'account_context': {'data_status': {'deep_conversion_coverage': {'1d': {'available_days':['2026-09-14'], 'missing_dates':[], 'blank_value_dates':{}, 'valid_leads':0}}}}})
    assert '素材正文全部缺失' in report
class FixtureModel:
    def __init__(self):
        self.routes = iter(['data_analysis', 'ads_diagnosis', 'content_review', 'strategy', 'finish'])
    def bind_tools(self, tools):
        from langchain_core.messages import AIMessage
        class Bound:
            def invoke(self, messages):
                return AIMessage(content='Fixture has no additional tool request.')
        return Bound()
    def with_structured_output(self, schema, **kwargs):
        owner=self
        class Bound:
            def invoke(self, messages):
                if schema.__name__ == 'SupervisorDecision':
                    target=next(owner.routes)
                    return schema(next_agent=target, task='fixture', reason='fixture', can_finish=target=='finish')
                if schema.__name__ == 'StrategyResult':
                    return schema(overall_judgement='深转（石墨）全窗口缺失。', cannot_conclude=['fixture'])
                return schema(summary='fixture')
        return Bound()


@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.parametrize("snapshot_kind", ["valid", "wrong_account", "invalid_json"])
def test_daily_entry_full_graph_writes_evidence_and_corrected_report(tmp_path, mismatch, snapshot_kind):
    import pandas as pd
    import daily_job
    from services.daily_agent_runner import run_daily_agent
    from replay_core import UNIT_COLUMNS, MATERIAL_COLUMNS
    account=tmp_path/'account'; account.mkdir()
    values={'id':'123','name':'fixture','date':'2026-09-14','spend':1,'impressions':10,'clicks':1,'leads':0,'private_messages':0,'campaign_type':'fixture'}
    for name, columns in [('unit',UNIT_COLUMNS),('material',MATERIAL_COLUMNS)]:
        row = {column: values[key] for key,column in columns.items()}
        if mismatch and name == 'material':
            row[columns['spend']] = 0.5
        pd.DataFrame([row]).to_excel(account/(name+'.xlsx'),index=False)
    snapshot=tmp_path/'shimo.json'
    snapshot.write_text(json.dumps({'account_id':'account','rows':[{'data_date':'2026-09-14','spend':1,'valid_leads':0,'invalid_leads':0}]}),encoding='utf-8')
    if snapshot_kind == "wrong_account":
        snapshot.write_text(json.dumps({'account_id':'other','rows':[]}),encoding='utf-8')
    if snapshot_kind == "invalid_json":
        snapshot.write_text('{broken',encoding='utf-8')
    settings=Settings(agent_enabled=True)
    result=daily_job.run_daily.__wrapped__(input_dir=account, output_root=tmp_path/'out',report_date='2026-09-14',shimo_snapshot=snapshot,settings=settings,agent_runner=lambda request: run_daily_agent(request,settings=settings,client=FixtureModel()))
    if snapshot_kind != 'valid':
        assert result['status'] == 'fail'
        assert (tmp_path/'out'/result['run_id']/'failure.json').exists()
        return
    assert result['status']==('partial' if mismatch else 'pass')
    assert result['pipeline_mode']=='multi_agent'
    report=Path(result['report']); assert report.exists()
    text=report.read_text(encoding='utf-8')
    if mismatch:
        assert '对账未通过' in text
        assert 'spend=0.5' in text
    assert '全窗口缺失' not in text
    assert '有效深转合计=0' in text
    state=json.loads((report.parent/'state.json').read_text(encoding='utf-8'))
    assert state['push_result']['status']=='disabled'
    assert '全窗口缺失' in state['strategy_result']['overall_judgement']
    assert all((report.parent/file).exists() for file in ('manifest.json','review.json','events.jsonl'))


@pytest.mark.parametrize('claim', ['石墨今日数据全部缺失，今日深转判断需等待补齐。', '深转成本全部缺失。'])
def test_scoped_missing_claim_is_preserved(claim):
    report = render_daily_report({'strategy_result': {'overall_judgement': claim},
        'account_context': {'data_status': {'deep_conversion_coverage': {
            '3d': {'available_days': ['2026-09-14'], 'missing_dates': ['2026-09-16'],
                   'blank_value_dates': {'spend': ['2026-09-14']}, 'valid_leads': None}}}}})
    assert claim in report
