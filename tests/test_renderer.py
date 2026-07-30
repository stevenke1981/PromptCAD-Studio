from __future__ import annotations

from pathlib import Path

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

    monkeypatch.setattr(renderer, "_run", capture_run)

    result = renderer.render(job_dir, ["step"])

    assert result.status == "completed"
    assert Path(captured["command"][1]).is_absolute()
    assert captured["cwd"].is_absolute()
