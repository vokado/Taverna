import pytest
from taverna.core import AppError
from taverna.settings import SettingsPatch, SettingsService, WorkspaceRequest
from taverna.storage import JsonStore


def test_levels_preserve_expert_values_and_conflicts(tmp_path):
    service = SettingsService(JsonStore(tmp_path))
    result = service.patch(
        SettingsPatch(
            expected_revision=0, patch={"level": "expert", "expert": {"seed": 42, "extra": {"value": 9}}}
        )
    )
    result = service.patch(SettingsPatch(expected_revision=result["revision"], patch={"level": "simple"}))
    assert result["settings"]["expert"]["seed"] == 42
    assert SettingsService(JsonStore(tmp_path)).get() == result
    with pytest.raises(AppError) as conflict:
        service.patch(SettingsPatch(expected_revision=1, patch={"theme": "light"}))
    assert conflict.value.code == "storage_conflict"
    with pytest.raises(AppError):
        service.patch(
            SettingsPatch(expected_revision=2, patch={"colors": {"text": "url(https://evil.test)"}})
        )
    assert service.get() == result
    result = service.patch(SettingsPatch(expected_revision=2, patch={"expert": {}}))
    assert result["settings"]["expert"] == {}


def test_workspace_is_separate_and_survives_restart(tmp_path):
    service = SettingsService(JsonStore(tmp_path))
    payload = {
        "name": "Моя Таверна",
        "immersive": True,
        "panels": [{"id": "models", "width": 600, "collapsed": False}],
    }
    saved = service.save_workspace(WorkspaceRequest(expected_revision=0, workspace=payload))
    result = service.patch(SettingsPatch(expected_revision=0, patch={"active_workspace": saved["id"]}))
    assert result["settings"]["active_workspace"] == saved["id"]
    payload["panels"][0]["collapsed"] = True
    saved = service.save_workspace(WorkspaceRequest(expected_revision=1, workspace=payload), saved["id"])
    assert saved["revision"] == 2
    assert JsonStore(tmp_path).scan("chats/")[0] == []
