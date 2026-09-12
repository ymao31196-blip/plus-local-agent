import pytest

from transaction_runtime import ActionTransactionStore


def _steps():
    return [
        {
            "step_id": "prepare",
            "title": "Prepare migration",
            "kind": "action",
        },
        {
            "step_id": "install",
            "title": "Install target",
            "kind": "action",
            "rollback_step_id": "rollback_install",
        },
        {
            "step_id": "verify",
            "title": "Verify target",
            "kind": "verify",
        },
        {
            "step_id": "rollback_install",
            "title": "Rollback target install",
            "kind": "rollback",
        },
    ]


def test_create_transaction_persists_plan_and_revision():
    store = ActionTransactionStore()
    try:
        record = store.create(
            "Move Example App to D drive",
            _steps(),
            {"domain": "software_migration"},
        )

        assert record["status"] == "planned"
        assert record["revision"] == 1
        assert record["goal"] == "Move Example App to D drive"
        assert [step["state"] for step in record["steps"]] == [
            "pending",
            "pending",
            "pending",
            "pending",
        ]
        assert record["metadata"]["domain"] == "software_migration"
        assert record["events"][0]["type"] == "transaction_created"
    finally:
        store.close()


def test_create_rejects_invalid_rollback_reference():
    store = ActionTransactionStore()
    try:
        steps = [
            {
                "step_id": "write",
                "title": "Write",
                "kind": "action",
                "rollback_step_id": "missing",
            }
        ]
        with pytest.raises(ValueError, match="Unknown rollback_step_id"):
            store.create("test", steps)
    finally:
        store.close()


def test_checkpoint_uses_optimistic_revision():
    store = ActionTransactionStore()
    try:
        record = store.create("test", _steps())
        updated = store.checkpoint(
            record["transaction_id"],
            1,
            "prepare",
            "succeeded",
            "prepared",
        )
        assert updated["revision"] == 2

        with pytest.raises(ValueError, match="changed since inspection"):
            store.checkpoint(
                record["transaction_id"],
                1,
                "install",
                "started",
                "stale",
            )
    finally:
        store.close()


def test_action_success_stays_active_until_all_actions_finish():
    store = ActionTransactionStore()
    try:
        record = store.create("test", _steps())
        record = store.checkpoint(
            record["transaction_id"],
            record["revision"],
            "prepare",
            "succeeded",
            "prepared",
        )

        assert record["status"] == "active"

        record = store.checkpoint(
            record["transaction_id"],
            record["revision"],
            "install",
            "succeeded",
            "installed",
        )

        assert record["status"] == "verification_pending"
    finally:
        store.close()


def test_happy_path_requires_verification_before_commit():
    store = ActionTransactionStore()
    try:
        record = store.create("test", _steps())
        tx = record["transaction_id"]

        record = store.checkpoint(tx, 1, "prepare", "succeeded", "prepared")
        record = store.checkpoint(tx, 2, "install", "succeeded", "installed")

        with pytest.raises(ValueError, match="Commit requires"):
            store.finalize(tx, record["revision"], "commit", "too early")

        record = store.checkpoint(
            tx,
            record["revision"],
            "verify",
            "verified",
            "verification passed",
            {"location": r"D:\Apps\Example"},
        )
        record = store.checkpoint(
            tx,
            record["revision"],
            "rollback_install",
            "skipped",
            "rollback not needed",
        )
        record = store.finalize(
            tx,
            record["revision"],
            "commit",
            "migration verified",
        )

        assert record["status"] == "committed"
        assert record["final_summary"] == "migration verified"
    finally:
        store.close()


def test_failure_after_mutation_requires_rollback():
    store = ActionTransactionStore()
    try:
        record = store.create("test", _steps())
        tx = record["transaction_id"]
        record = store.checkpoint(tx, 1, "prepare", "succeeded", "prepared")
        record = store.checkpoint(tx, 2, "install", "succeeded", "installed")
        record = store.checkpoint(
            tx,
            record["revision"],
            "verify",
            "failed",
            "target verification failed",
        )

        assert record["status"] == "rollback_required"

        record = store.checkpoint(
            tx,
            record["revision"],
            "rollback_install",
            "rolled_back",
            "removed failed target",
        )
        record = store.finalize(
            tx,
            record["revision"],
            "rolled_back",
            "rollback completed",
        )
        assert record["status"] == "rolled_back"
    finally:
        store.close()


def test_failure_without_completed_rollbackable_action_blocks():
    store = ActionTransactionStore()
    try:
        record = store.create("test", _steps())
        record = store.checkpoint(
            record["transaction_id"],
            1,
            "prepare",
            "failed",
            "preflight failed",
        )

        assert record["status"] == "blocked"
    finally:
        store.close()


def test_abort_is_allowed_for_open_transaction():
    store = ActionTransactionStore()
    try:
        record = store.create("test", _steps())
        record = store.finalize(
            record["transaction_id"],
            1,
            "abort",
            "owner stopped operation",
        )
        assert record["status"] == "aborted"
    finally:
        store.close()


def test_restart_marks_running_step_interrupted(tmp_path):
    db_path = tmp_path / "transactions.sqlite3"
    first = ActionTransactionStore(db_path=db_path)
    record = first.create("test", _steps())
    record = first.checkpoint(
        record["transaction_id"],
        1,
        "install",
        "started",
        "external action started",
    )
    tx = record["transaction_id"]
    first.close()

    second = ActionTransactionStore(db_path=db_path)
    try:
        recovered = second.get(tx)
        install = next(
            step for step in recovered["steps"] if step["step_id"] == "install"
        )

        assert recovered["status"] == "blocked"
        assert recovered["revision"] == 3
        assert install["state"] == "interrupted"
        assert recovered["events"][-1]["type"] == "runtime_recovery"
    finally:
        second.close()


def test_store_survives_normal_reopen(tmp_path):
    db_path = tmp_path / "transactions.sqlite3"
    first = ActionTransactionStore(db_path=db_path)
    record = first.create("persist me", _steps())
    tx = record["transaction_id"]
    first.close()

    second = ActionTransactionStore(db_path=db_path)
    try:
        reopened = second.get(tx)
        assert reopened["goal"] == "persist me"
        assert reopened["revision"] == 1
    finally:
        second.close()


def test_server_transaction_tools_roundtrip():
    import server

    record = server.transaction_create(
        "Server wrapper test",
        [
            {"step_id": "act", "title": "Act", "kind": "action"},
            {"step_id": "verify", "title": "Verify", "kind": "verify"},
        ],
        {"source": "test"},
    )
    tx = record["transaction_id"]

    record = server.transaction_checkpoint(
        tx,
        record["revision"],
        "act",
        "succeeded",
        "action completed",
        {"ok": True},
    )
    record = server.transaction_checkpoint(
        tx,
        record["revision"],
        "verify",
        "verified",
        "verification completed",
        {"ok": True},
    )
    record = server.transaction_finalize(
        tx,
        record["revision"],
        "commit",
        "wrapper roundtrip complete",
    )

    assert record["status"] == "committed"
    assert server.transaction_get(tx)["revision"] == record["revision"]
