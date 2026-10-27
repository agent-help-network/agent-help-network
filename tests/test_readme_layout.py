"""A7: the README (English and Korean) opens with install, a real response, when to call it, and limits."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EN = ["Install", "A real response", "When to call it", "Limits", "REST and MCP reference", "What the answer contains",
      "What we read and never do", "Documentation"]
KO = ["설치", "실제 응답", "언제 부르나", "한계", "REST·MCP 참고", "응답의 구성", "읽는 것과 절대 하지 않는 것", "문서"]


def _headings(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    return text, re.findall(r"^## (.+?)\s*$", text, flags=re.M)


def test_readme_sections_in_adoption_order():
    for name, want in (("README.md", EN), ("README.ko.md", KO)):
        _, found = _headings(name)
        assert found[:len(want)] == want, (name, found)


def test_readme_example_uses_the_contract_keys_and_pairs_match():
    for name in ("README.md", "README.ko.md"):
        text, _ = _headings(name)
        for key in ('"summary_line"', '"next_action"', '"links"', '"recommendation"'):
            assert key in text, (name, key)
        assert text.index("## " + (EN[1] if name == "README.md" else KO[1])) < text.index('"summary_line"')
    en, ko = (ROOT / "README.md").read_text(encoding="utf-8"), (ROOT / "README.ko.md").read_text(encoding="utf-8")
    assert en.count("```") == ko.count("```")


def test_readme_anchors_resolve():
    for name in ("README.md", "README.ko.md"):
        text, heads = _headings(name)
        anchors = {re.sub(r"[^\w\- 가-힣]", "", h.lower()).replace(" ", "-") for h in heads}
        for target in re.findall(r"\]\(#([^)]+)\)", text):
            assert target in anchors, (name, target)
