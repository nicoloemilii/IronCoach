"""Build and verify the local audit release against the published byte baseline.

Run with --inspect to report the pending delta without emitting release files.
All reads use raw bytes so CRLF in published sources stays authoritative.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[2]
QA = (ROOT / ".qa").resolve()
FOLDER = QA / "goal-response"
BASELINE = FOLDER
PUBLISHED_COMMIT = None  # Local baseline; this follow-up is not published.
PUBLISHED_TREE = "ef0aba510e86a9223ed38190b4641a3a452ce3dc"
SOURCE_DIRS = ("app", "web", "tests", "docs", "desktop", "ios", "deploy", ".github")
EXTENSIONS = {".py", ".js", ".css", ".html", ".cjs", ".json", ".txt", ".md", ".swift", ".yml", ".yaml", ".lock"}
ROOT_FILES = {".gitignore", ".dockerignore", ".env.example", "Dockerfile", "compose.yaml", "requirements-web.lock"}
EXCLUDED_DIRS = {".qa", "data", ".venv", ".venv-web", "venv", "node_modules", "__pycache__", ".secrets", ".credentials", ".git", ".pytest_cache"}
EXCLUDED_NAMES = {"credentials.json", "service-account.json", "token.json", ".npmrc", ".pypirc"}
SECRET_PATTERNS = {
    "github_token": rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}",
    "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "stripe_live_key": rb"[sr]k_live_[A-Za-z0-9]{20,}",
    "stripe_webhook_secret": rb"whsec_[A-Za-z0-9]{20,}",
    "aws_access_key": rb"(?:AKIA|ASIA)[A-Z0-9]{16}",
    "slack_token": rb"xox[baprs]-[A-Za-z0-9-]{24,}",
    "google_api_key": rb"AIza[A-Za-z0-9_-]{35}",
}


def git_object(kind: str, data: bytes) -> str:
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def tree_hash(files: dict[str, str]) -> str:
    tree = {}
    for path, sha in files.items():
        node = tree
        pieces = path.split("/")
        for piece in pieces[:-1]:
            node = node.setdefault(piece, {})
        node[pieces[-1]] = sha

    def digest(node):
        content = b""
        for name, value in sorted(node.items(), key=lambda item: (item[0] + ("/" if isinstance(item[1], dict) else "")).encode("utf-8")):
            directory = isinstance(value, dict)
            sha = digest(value) if directory else value
            content += (b"40000" if directory else b"100644") + b" " + name.encode("utf-8") + b"\0" + bytes.fromhex(sha)
        return git_object("tree", content)

    return digest(tree)


def safe_path(parent: Path, relative: str) -> Path:
    parsed = PurePosixPath(relative)
    assert not parsed.is_absolute() and ".." not in parsed.parts and "\\" not in relative, relative
    candidate = parent.joinpath(*parsed.parts)
    assert candidate.resolve().is_relative_to(parent.resolve()), relative
    return candidate


def load_json(path: Path):
    return json.loads(path.read_bytes().decode("ascii"))


def json_bytes(value, *, indent=None) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=indent) + "\n").encode("ascii")


def published_files() -> dict[str, bytes]:
    base = {entry["path"]: entry["content"].encode("utf-8") for entry in load_json(BASELINE / "before.json")}
    assert len(base) == 203, len(base)
    actual = tree_hash({path: git_object("blob", data) for path, data in base.items()})
    assert actual == PUBLISHED_TREE, actual
    return base


def source_files() -> dict[str, bytes]:
    paths = []
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*"):
            relative = path.relative_to(ROOT)
            if path.is_file() and not any(piece in EXCLUDED_DIRS for piece in relative.parts) and path.name not in EXCLUDED_NAMES:
                if path.suffix.lower() in EXTENSIONS or path.name == "Caddyfile":
                    paths.append(path)
    paths.extend(path for path in ROOT.iterdir() if path.is_file() and (path.name in ROOT_FILES or path.suffix.lower() in {".md", ".txt", ".bat"}))
    files = {}
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT).as_posix()
        assert path.resolve().is_relative_to(ROOT), relative
        assert not any(piece in EXCLUDED_DIRS for piece in PurePosixPath(relative).parts), relative
        assert path.name not in EXCLUDED_NAMES and (not path.name.startswith(".env") or path.name == ".env.example"), relative
        data = path.read_bytes()
        data.decode("utf-8")
        files[relative] = data
    assert "deploy/Caddyfile" in files and "requirements-web.lock" in files
    return files


def normalized_newlines(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def newline_stats(data: bytes) -> dict[str, int]:
    return {"crlf": data.count(b"\r\n"), "bare_lf": data.count(b"\n") - data.count(b"\r\n")}


def make_patch(base: dict[str, bytes], files: dict[str, bytes]) -> bytes:
    patch = []
    for path in sorted(set(base) | set(files)):
        before, after = base.get(path, b""), files.get(path, b"")
        if path in base and path in files and before == after:
            continue
        patch.append(f"diff --git a/{path} b/{path}\n")
        if path not in base:
            patch.append("new file mode 100644\n")
        if path not in files:
            patch.append("deleted file mode 100644\n")
        patch.append(f"index {git_object('blob', before) if path in base else '0' * 40}..{git_object('blob', after) if path in files else '0' * 40}\n")
        lines = difflib.unified_diff(before.decode("utf-8").splitlines(True), after.decode("utf-8").splitlines(True), fromfile=f"a/{path}" if path in base else "/dev/null", tofile=f"b/{path}" if path in files else "/dev/null")
        for line in lines:
            patch.append(line)
            if not line.endswith("\n"):
                patch.append("\n\\ No newline at end of file\n")
    return "".join(patch).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    assert QA.is_relative_to(ROOT) and FOLDER.resolve().is_relative_to(QA)
    base, files = published_files(), source_files()
    changed = [path for path in sorted(set(base) | set(files)) if base.get(path) != files.get(path)]
    newline_only = [path for path in changed if path in base and path in files and normalized_newlines(base[path]) == normalized_newlines(files[path])]
    newline_changes = [{"path": path, "before": newline_stats(base[path]), "after": newline_stats(files[path]), "newline_only": path in newline_only} for path in changed if path in base and path in files and newline_stats(base[path]) != newline_stats(files[path])]
    secret_hits = [{"path": path, "pattern": name} for path, data in files.items() for name, pattern in SECRET_PATTERNS.items() if re.search(pattern, data)]
    assert not secret_hits, json.dumps(secret_hits)
    report = {"files": len(files), "changed_files": len(changed), "baseline_tree_sha": PUBLISHED_TREE, "expected_tree_sha": tree_hash({path: git_object("blob", data) for path, data in files.items()}), "newline_only_paths": newline_only, "newline_changes": newline_changes, "changed_paths": changed, "secret_scan": {"patterns": sorted(SECRET_PATTERNS), "files_scanned": len(files), "hits": secret_hits}}
    if args.inspect:
        print(json.dumps(report))
        return
    assert "docs/APP_AUDIT_2026_10_19.md" in files, "Wait for the final audit document before packaging"
    blobs = {path: git_object("blob", data) for path, data in files.items()}
    manifest = [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "git_blob_sha": blobs[path]} for path, data in files.items()]
    entries = [{"path": path, "mode": "100644", "type": "blob", "content": files[path].decode("utf-8") if path in files else None} for path in changed]
    patch_path = FOLDER / "IronCoach-Training-response.diff"
    package_path = FOLDER / "IronCoach-Training-response-sorgenti.zip"
    patch_path.write_bytes(make_patch(base, files))
    parts, part = [], []
    for entry in entries:
        if part and len(json_bytes(part + [entry])) > 60000:
            parts.append(part)
            part = []
        part.append(entry)
    if part:
        parts.append(part)
    expected_parts = {f"part-{index}.json" for index in range(len(parts))}
    for stale in FOLDER.glob("part-*.json"):
        assert stale.resolve().is_relative_to(QA)
        if stale.name not in expected_parts:
            stale.unlink()
    part_metadata = []
    for index, part in enumerate(parts):
        name = f"part-{index}.json"
        data = json_bytes(part)
        (FOLDER / name).write_bytes(data)
        part_metadata.append({"path": name, "bytes": len(data), "entries": len(part), "oversized_single_entry": len(data) > 60000 and len(part) == 1, "sha256": hashlib.sha256(data).hexdigest()})
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            info = zipfile.ZipInfo(path, (2026, 10, 19, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(package_path) as archive:
        assert set(archive.namelist()) == set(files)
        for path, data in files.items():
            actual = archive.read(path)
            assert actual == data == (ROOT / path).read_bytes(), path
            assert git_object("blob", actual) == blobs[path]
    patch_check = (FOLDER / "patch-check").resolve()
    assert patch_check.is_relative_to(QA) and patch_check != QA
    patch_check.mkdir(exist_ok=True)
    for path, data in base.items():
        target = safe_path(patch_check, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    assert {path.relative_to(patch_check).as_posix() for path in patch_check.rglob("*") if path.is_file()} == set(base)
    check = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn", str(patch_path)], cwd=patch_check, capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    (FOLDER / "patch-check.log").write_bytes((check.stdout + check.stderr + "git apply --check: PASS\n").encode("utf-8"))
    # Independently apply the delta content and hash the resulting complete tree.
    reconstructed = dict(base)
    for metadata in part_metadata:
        for entry in load_json(FOLDER / metadata["path"]):
            if entry["content"] is None:
                reconstructed.pop(entry["path"])
            else:
                reconstructed[entry["path"]] = entry["content"].encode("utf-8")
    assert reconstructed == files
    assert tree_hash({path: git_object("blob", data) for path, data in reconstructed.items()}) == report["expected_tree_sha"]
    audit = dict(report, baseline_commit=PUBLISHED_COMMIT, baseline_snapshot_sha256=hashlib.sha256((BASELINE / "before.json").read_bytes()).hexdigest(), parts=len(parts), delta_parts=part_metadata, zip_sha256=hashlib.sha256(package_path.read_bytes()).hexdigest(), diff_sha256=hashlib.sha256(patch_path.read_bytes()).hexdigest(), all_exact=True, git_apply_check=True, delta_reconstruction_exact=True, zip_path=package_path.relative_to(ROOT).as_posix(), diff_path=patch_path.relative_to(ROOT).as_posix())
    (FOLDER / "blobs.json").write_bytes(json_bytes(blobs, indent=2))
    (FOLDER / "manifest.json").write_bytes(json_bytes(manifest, indent=2))
    (FOLDER / "audit.json").write_bytes(json_bytes(audit, indent=2))
    print(json.dumps(audit))


if __name__ == "__main__":
    main()
