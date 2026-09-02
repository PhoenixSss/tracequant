import subprocess
from pathlib import Path
from zipfile import ZipFile

import tracequant


def test_package_name() -> None:
    assert tracequant.__name__ == "tracequant"


def test_package_license_metadata(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    distribution_dir = tmp_path / "dist"
    distribution_dir.mkdir()

    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(distribution_dir),
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    wheels = sorted(distribution_dir.glob("*.whl"))
    assert len(wheels) == 1

    with ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        metadata_paths = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        license_paths = [
            name for name in names if name.endswith(".dist-info/licenses/LICENSE")
        ]

        assert len(metadata_paths) == 1
        assert len(license_paths) == 1

        metadata = wheel.read(metadata_paths[0]).decode("utf-8")
        packaged_license = wheel.read(license_paths[0]).decode("utf-8")

    assert "License-Expression: Apache-2.0" in metadata
    assert "License-File: LICENSE" in metadata
    assert packaged_license == (project_root / "LICENSE").read_text(encoding="utf-8")
