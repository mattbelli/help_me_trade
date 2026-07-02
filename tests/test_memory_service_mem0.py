"""MemoryService <-> mem0 mirroring: delete must not drift from add/update."""

from __future__ import annotations

from unittest.mock import MagicMock

import ai.memories as mem_mod
from ai.memories import MemoryService


def _service_with_mock_mem0() -> tuple[MemoryService, MagicMock]:
    # Skip __init__ (which would try a real mem0 client) and inject a mock directly,
    # matching how add()/update() only ever touch self._mem0 through the guard below.
    service = MemoryService.__new__(MemoryService)
    mock_mem0 = MagicMock()
    service._mem0 = mock_mem0
    return service, mock_mem0


def test_delete_mirrors_to_mem0_when_active(monkeypatch) -> None:
    monkeypatch.setattr(mem_mod, "delete_memory", lambda memory_id: True)
    service, mock_mem0 = _service_with_mock_mem0()
    mock_mem0.get_all.return_value = {"results": [{"id": "mem0-abc"}]}

    assert service.delete(42) is True

    mock_mem0.get_all.assert_called_once_with(
        filters={"user_id": mem_mod.USER_ID, "local_id": 42}
    )
    mock_mem0.delete.assert_called_once_with("mem0-abc")


def test_delete_mem0_failure_does_not_break_local_delete(monkeypatch) -> None:
    monkeypatch.setattr(mem_mod, "delete_memory", lambda memory_id: True)
    service, mock_mem0 = _service_with_mock_mem0()
    mock_mem0.get_all.side_effect = RuntimeError("mem0 down")

    # Fail-soft: mirroring blows up, but the local SQLite delete already succeeded.
    assert service.delete(42) is True


def test_delete_skips_mem0_when_local_delete_finds_nothing(monkeypatch) -> None:
    monkeypatch.setattr(mem_mod, "delete_memory", lambda memory_id: False)
    service, mock_mem0 = _service_with_mock_mem0()

    assert service.delete(999) is False
    mock_mem0.get_all.assert_not_called()


def test_delete_inactive_mem0_is_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(mem_mod, "delete_memory", lambda memory_id: True)
    service = MemoryService.__new__(MemoryService)
    service._mem0 = None

    assert service.delete(1) is True


def test_add_tags_mem0_metadata_with_local_id(monkeypatch) -> None:
    record = {"id": 7, "type": "preference", "content": "Cap at 20%"}
    monkeypatch.setattr(mem_mod, "create_memory", lambda **_kwargs: record)
    service, mock_mem0 = _service_with_mock_mem0()

    service.add("Cap at 20%", memory_type="preference")

    _, kwargs = mock_mem0.add.call_args
    assert kwargs["metadata"]["local_id"] == 7
