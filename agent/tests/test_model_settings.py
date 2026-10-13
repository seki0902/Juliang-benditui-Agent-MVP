import io
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import pytest


def store(tmp_path):
    from services.local_model_config import ModelConfigStore
    return ModelConfigStore(tmp_path, environment={})


def test_existing_env_txt_is_read_without_exposing_key(tmp_path):
    (tmp_path / '.env.txt').write_text('DEEPSEEK_API_KEY=secret-example\nAGENT_MODEL=fixture-model\n', encoding='utf-8')
    config = store(tmp_path)
    view = config.public()
    assert view['model'] == 'fixture-model'
    assert view['key_configured'] is True
    assert 'secret-example' not in json.dumps(view)
    assert config.overrides()['deepseek_api_key'] == 'secret-example'


def test_save_and_reload_retains_blank_key_and_other_provider(tmp_path):
    config = store(tmp_path)
    config.save({'provider': 'deepseek', 'model': 'fixture-d', 'api_key': 'secret-d'})
    config.save({'provider': 'openai', 'model': 'fixture-o', 'api_key': 'secret-o'})
    view = config.save({'provider': 'deepseek', 'model': 'new-d', 'api_key': ''})
    assert view['key_configured'] is True
    assert 'secret-' not in json.dumps(view)
    assert store(tmp_path).overrides()['deepseek_api_key'] == 'secret-d'
    assert store(tmp_path).overrides()['openai_api_key'] == 'secret-o'


@pytest.mark.parametrize('payload', [
    {'provider': 'unknown', 'model': 'fixture'},
    {'provider': 'deepseek', 'model': ''},
    {'provider': 'deepseek', 'model': 'fixture', 'api_key': 'key\nINJECT=true'},
])
def test_invalid_settings_are_not_written(tmp_path, payload):
    with pytest.raises(ValueError):
        store(tmp_path).save(payload)
    assert not (tmp_path / 'state/model_settings.json').exists()


def test_connection_uses_candidate_without_saving_or_exposing_key(tmp_path):
    config = store(tmp_path)
    config.save({'provider':'deepseek', 'model':'before', 'api_key':'secret-d'})
    response = io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
    with patch('services.local_model_config.urlopen', return_value=response) as request:
        result = config.test({'provider':'deepseek', 'model':'candidate', 'api_key':''})
    assert result['status'] == 'pass'
    assert result['model'] == 'candidate'
    assert request.call_args.args[0].get_header('Authorization') == 'Bearer secret-d'
    assert config.public()['model'] == 'before'
    assert 'secret-d' not in json.dumps(result)


def test_authentication_error_is_readable_and_does_not_leak_response(tmp_path):
    error = HTTPError('https://api.deepseek.com/chat/completions', 401, 'unauthorized', {},
                      io.BytesIO(b'{"message":"secret-example is invalid"}'))
    with patch('services.local_model_config.urlopen', side_effect=error):
        result = store(tmp_path).test({'provider':'deepseek', 'model':'fixture', 'api_key':'secret-example'})
    assert result['status'] == 'fail'
    assert result['http_status'] == 401
    assert 'secret-example' not in json.dumps(result)


def test_process_environment_has_precedence(tmp_path):
    from services.local_model_config import ModelConfigStore
    config = ModelConfigStore(tmp_path, environment={'DEEPSEEK_API_KEY':'process-key', 'AGENT_MODEL':'process-model'})
    config.save({'provider':'deepseek', 'model':'saved-model', 'api_key':'saved-key'})
    assert config.overrides()['deepseek_api_key'] == 'process-key'
    assert config.public()['model'] == 'process-model'
    assert config.public()['environment_override'] is True


def test_saved_ui_configuration_is_loaded_by_runtime(tmp_path):
    from config import Settings
    config = store(tmp_path)
    config.save({'provider':'deepseek', 'model':'ui-model', 'api_key':'ui-secret'})
    with patch.dict(os.environ, {}, clear=True), patch(
        'services.local_model_config.ModelConfigStore', return_value=config):
        settings = Settings.from_env()
    assert settings.agent_model == 'ui-model'
    assert settings.deepseek_api_key == 'ui-secret'
    assert settings.agent_enabled is False


def test_saved_configuration_overrides_env_file_but_not_process_env(tmp_path):
    from services.local_model_config import ModelConfigStore
    (tmp_path / '.env').write_text('AGENT_MODEL=old-model\nDEEPSEEK_API_KEY=old-key\n', encoding='utf-8')
    config = ModelConfigStore(tmp_path, environment={})
    assert config.public()['model'] == 'old-model'
    config.save({'provider':'deepseek','model':'new-model','api_key':'new-key'})
    assert config.public()['model'] == 'new-model'
    assert config.overrides(include_legacy=False)['deepseek_api_key'] == 'new-key'


def test_legacy_env_loader_leaves_model_values_for_shared_resolver(tmp_path):
    import config
    env_file = tmp_path / '.env'
    env_file.write_text('AGENT_MODEL=old-model\nDEEPSEEK_API_KEY=old-key\nOTHER_VALUE=example\n', encoding='utf-8')
    with patch.dict(os.environ, {}, clear=True):
        config._load_local_env(env_file)
        assert os.environ.get('AGENT_MODEL') is None
        assert os.environ.get('DEEPSEEK_API_KEY') is None
        assert os.environ['OTHER_VALUE'] == 'example'
