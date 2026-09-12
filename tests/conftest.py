"""Keep runtime tests out of the real durable database."""
import pytest


@pytest.fixture(autouse=True)
def isolated_server_task_store(tmp_path, monkeypatch):
    import server
    from task_store import TaskStore
    from transaction_runtime import ActionTransactionStore
    monkeypatch.setenv("AGENT_TASK_DB", str(tmp_path / "subprocess-state" / "tasks.sqlite3"))
    monkeypatch.setenv(
        "AGENT_TRANSACTION_DB",
        str(tmp_path / "subprocess-state" / "transactions.sqlite3"),
    )
    store = TaskStore(db_path=tmp_path / "server-state" / "tasks.sqlite3")
    transaction_store = ActionTransactionStore(
        db_path=tmp_path / "server-state" / "transactions.sqlite3"
    )
    monkeypatch.setattr(server, "TASK_STORE", store)
    monkeypatch.setattr(server, "TRANSACTION_STORE", transaction_store)
    yield
    transaction_store.close()
    store.close()
