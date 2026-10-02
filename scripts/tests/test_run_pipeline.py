"""CLI cache errors remain safe; explicit force can recover a corrupt local export."""

import json
from pathlib import Path

import pytest

from scripts.run_pipeline import main


@pytest.mark.parametrize("force", [False, True])
def test_corrupt_export_cache_requires_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], force: bool
) -> None:
    source = tmp_path / "source.md"
    source.write_text("# Authentication\n\nValidate tokens before opening a session.")
    output = tmp_path / "export"
    output.mkdir()
    (output / "pipeline.json").write_text('{"private_content":"never expose this payload"}')
    monkeypatch.setattr(
        "sys.argv", ["run_pipeline", str(source), "--out", str(output), *(["--force"] if force else [])]
    )
    if force:
        main()
        assert json.loads((output / "pipeline.json").read_text())["chunks"]
        assert (output / "index.md").exists()
    else:
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
    captured = capsys.readouterr()
    assert "never expose this payload" not in captured.err + captured.out
