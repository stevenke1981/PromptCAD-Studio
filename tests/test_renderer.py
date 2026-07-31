from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.renderer import Renderer


def test_cadquery_renderer_resolves_relative_job_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    job_dir = Path("generated") / "job"
    job_dir.mkdir(parents=True)
    (job_dir / "model.py").write_text("print('model')", encoding="utf-8")

    renderer = Renderer(
        Settings(
            env="test",
            data_dir=Path("generated"),
            render_backend="cadquery",
            allow_source_fallback=False,
        )
    )
    monkeypatch.setattr(renderer, "cadquery_available", lambda: True)
    captured = {}

    def capture_run(command, cwd):
        captured["command"] = command
        captured["cwd"] = cwd
        (cwd / "model.step").write_text(
            "ISO-10303-21;\nEND-ISO-10303-21;\n",
            encoding="ascii",
        )

    monkeypatch.setattr(renderer, "_run", capture_run)

    result = renderer.render(job_dir, ["step"])

    assert result.status == "completed"
    assert Path(captured["command"][1]).is_absolute()
    assert captured["cwd"].is_absolute()
    assert result.fallback_chain == ["cadquery"]


def test_renderer_rejects_exit_zero_without_requested_output(monkeypatch, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "model.py").write_text("print('model')", encoding="utf-8")
    renderer = Renderer(
        Settings(
            env="test",
            data_dir=tmp_path,
            render_backend="cadquery",
            allow_source_fallback=False,
        )
    )
    monkeypatch.setattr(renderer, "cadquery_available", lambda: True)
    monkeypatch.setattr(renderer, "_run", lambda _command, _cwd: None)

    with pytest.raises(RuntimeError, match="did not produce requested step"):
        renderer.render(job_dir, ["step"])


def test_build123d_does_not_start_runner_without_supported_format(
    monkeypatch,
    tmp_path,
):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "model.build123d.py").write_text("print('model')", encoding="utf-8")
    renderer = Renderer(
        Settings(
            env="test",
            data_dir=tmp_path,
            render_backend="build123d",
            allow_source_fallback=True,
        )
    )
    monkeypatch.setattr(renderer, "build123d_available", lambda: True)
    monkeypatch.setattr(
        renderer,
        "_run",
        lambda *_args: pytest.fail("runner must not be started"),
    )

    result = renderer.render(job_dir, ["json", "pdf"])

    assert result.status == "completed"
    assert result.renderer == "build123d"


def test_runtime_fallback_chain_is_reported(monkeypatch, tmp_path):
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "model.py").write_text("print('model')", encoding="utf-8")
    (job_dir / "model.scad").write_text("cube([1,1,1]);", encoding="utf-8")
    renderer = Renderer(
        Settings(
            env="test",
            data_dir=tmp_path,
            render_backend="cadquery",
            allow_source_fallback=True,
        )
    )
    monkeypatch.setattr(renderer, "cadquery_available", lambda: True)
    monkeypatch.setattr(renderer, "openscad_available", lambda: True)
    monkeypatch.setattr("app.services.renderer.shutil.which", lambda _name: "openscad")
    calls = 0

    def run_with_fallback(command, cwd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("cadquery failed")
        # Minimal structurally valid binary STL with one triangle.
        (cwd / "model.stl").write_bytes(
            b"\0" * 80 + (1).to_bytes(4, "little") + b"\0" * 50
        )

    monkeypatch.setattr(renderer, "_run", run_with_fallback)

    result = renderer.render(job_dir, ["stl"])

    assert result.status == "completed"
    assert result.renderer == "openscad"
    assert result.fallback_chain == ["cadquery", "openscad"]


def test_renderer_timeout_terminates_spawned_process_tree(tmp_path):
    marker = tmp_path / "descendant-marker.txt"
    child_code = (
        "import time; from pathlib import Path; "
        "time.sleep(1.0); "
        f"Path({str(marker)!r}).write_text('leaked', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )
    renderer = Renderer(
        Settings(
            env="test",
            data_dir=tmp_path,
            render_backend="cadquery",
            allow_source_fallback=False,
        )
    )
    renderer.settings.render_timeout_seconds = 0.2

    with pytest.raises(RuntimeError, match="timed out"):
        renderer._run([sys.executable, "-c", parent_code], tmp_path)

    time.sleep(1.2)
    assert not marker.exists()
