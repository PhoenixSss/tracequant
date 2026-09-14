from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import unquote

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STAGE2_REQUIREMENTS = Path("docs/product/stage-2-data-and-research-requirements.md")
EXPECTED_SHA256 = "877b794dc842a4b6d9a474f60ccff34f20674fabb26267279d9b2cd362fbdc58"
_MARKDOWN_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\(\s*<?(?P<target>[^>\)]+)>?\s*\)")


def test_stage2_requirements_are_tracked_and_discoverable() -> None:
    requirements_path = REPOSITORY_ROOT / STAGE2_REQUIREMENTS
    assert requirements_path.is_file()
    payload = requirements_path.read_bytes()
    assert hashlib.sha256(payload).hexdigest() == EXPECTED_SHA256

    document = payload.decode("utf-8")
    assert "| 文档版本 | `0.3` |" in document
    assert 'dataset_id = "binance-usdm-btceth-202001-202608-r1"' in document
    assert 'mark_price_interval = "15m"' in document
    assert "include_index_price = false" in document

    readme_path = REPOSITORY_ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    assert "OFFLINE_BACKTEST_ONLY" in readme
    assert "LIVE_NOT_APPROVED" in readme
    assert "Stage 1 behavior is unchanged" in readme
    assert "Stage 2 implementation baseline" in readme

    linked = False
    for match in _MARKDOWN_LINK.finditer(readme):
        target = unquote(match.group("target").strip())
        if "://" in target:
            continue
        relative_target = target.split("#", 1)[0]
        if not relative_target:
            continue
        resolved = (readme_path.parent / relative_target).resolve()
        if resolved == requirements_path.resolve():
            linked = True
            break
    assert linked
