import os

import pytest

from edgehunter.intelligence.environment import ALLOWED_KEYS, load_typesafe_env


def test_dotenv_reads_local_values_preserves_process_and_does_not_execute(tmp_path, monkeypatch):
    for key in ALLOWED_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TYPESAFE_MODEL", "process-model")
    path = tmp_path / ".env"
    path.write_text('TYPESAFE_API_KEY="local-secret" # comment\nTYPESAFE_MODEL=dotenv-model\n'
                    'export TYPESAFE_TIMEOUT_SECONDS=2 # comment\n'
                    'TYPESAFE_MAX_CALL_USD=$(touch never)\nPATH=BAD\n', encoding='utf-8')
    original_path = os.environ.get("PATH")
    load_typesafe_env(path)
    assert os.environ["TYPESAFE_API_KEY"] == "local-secret"
    assert os.environ["TYPESAFE_MODEL"] == "process-model"
    assert os.environ["TYPESAFE_TIMEOUT_SECONDS"] == "2"
    assert os.environ["TYPESAFE_MAX_CALL_USD"] == "$(touch never)"
    assert os.environ.get("PATH") == original_path


def test_invalid_env_error_does_not_include_secret(tmp_path):
    path = tmp_path / ".env"
    path.write_text('TYPESAFE_API_KEY="TOP_SECRET', encoding='utf-8')
    with pytest.raises(ValueError) as error:
        load_typesafe_env(path)
    assert "TOP_SECRET" not in str(error.value)
