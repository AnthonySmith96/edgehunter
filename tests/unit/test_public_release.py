import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "check_public_release", Path(__file__).resolve().parents[2] / "scripts/check_public_release.py",
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def test_release_check_rejects_secrets_and_personal_paths(tmp_path):
    (tmp_path / "safe.txt").write_text("public research")
    (tmp_path / "bad.env").write_text("TYPESAFE_" + "API_KEY=real-secret-value\n")
    (tmp_path / "path.txt").write_text("C:" + r"\Users\person\project\report.json")
    (tmp_path / "json.txt").write_text("C:" + r"\\Users\\person\\project\\report.json")

    findings = release.audit(tmp_path, ["safe.txt", "bad.env", "path.txt", "json.txt"])

    assert not any("safe.txt" in item for item in findings)
    assert any("TypeSafe" in item for item in findings)
    assert any("Windows user path" in item for item in findings)
    assert any("json.txt" in item for item in findings)


def test_release_check_allows_empty_key_template(tmp_path):
    (tmp_path / ".env.example").write_text("TYPESAFE_API_KEY=\nTYPESAFE_BUDGET_USD=0\n")
    assert release.audit(tmp_path, [".env.example"]) == []
