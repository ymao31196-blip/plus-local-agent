"""Keep runtime tests out of the real durable database."""
import pytest


@pytest.fixture(autouse=True)
def isolated_server_task_store(tmp_path, monkeypatch):
    import server
    from task_store import TaskStore
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "subprocess-state" / "tasks.sqlite3"))
    store = TaskStore(db_path=tmp_path / "server-state" / "tasks.sqlite3")
    monkeypatch.setattr(server, "TASK_STORE", store)
    yield
    store.close()
