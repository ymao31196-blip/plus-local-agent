import inspect

import server


def test_server_run_process_exposes_backend_selection():
    signature = inspect.signature(server.run_process)
    assert "backend" in signature.parameters
    assert signature.parameters["backend"].default == "default"


def test_server_run_process_forwards_backend(monkeypatch):
    captured = {}

    def fake_run_process(program, args, cwd, timeout, workdir, env, stdin, root, backend):
        captured.update(
            program=program,
            args=args,
            cwd=cwd,
            timeout=timeout,
            workdir=workdir,
            env=env,
            stdin=stdin,
            root=root,
            backend=backend,
        )
        return {"ok": True}

    monkeypatch.setattr(server, "internal_run_process", fake_run_process)
    for backend in ("candidate_runner", "shadow_candidate", "canary_candidate"):
        result = server.run_process("python", ["-c", "print('ok')"], backend=backend)
        assert result == {"ok": True}
        assert captured["backend"] == backend
