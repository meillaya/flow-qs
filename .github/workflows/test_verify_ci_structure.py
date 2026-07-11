#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run_gate(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, ".github/workflows/verify_ci_structure.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / "infra" / "cluster" / "main.tf"
    source = source_path.read_text(encoding="utf-8")
    executable_arg = '          --k3s-arg "--disable=traefik@server:*" \\'
    comment_only_arg = '          # --k3s-arg "--disable=traefik@server:*"'
    if source.count(executable_arg) != 1:
        print("test fixture requires one executable Traefik-disable argument", file=sys.stderr)
        return 1
    lint_path = repo_root / ".github" / "workflows" / "lint.yaml"
    lint_source = lint_path.read_text(encoding="utf-8")
    canonical_helm_command = "        run: make lint-chart"
    unsafe_helm_command = "        run: helm lint charts/*"
    if lint_source.count(canonical_helm_command) != 1:
        print("test fixture requires one canonical Helm lint command", file=sys.stderr)
        return 1
    test_path = repo_root / ".github" / "workflows" / "test.yaml"
    test_source = test_path.read_text(encoding="utf-8")
    canonical_pytest_command = "        run: uv run --frozen pytest"
    coverage_pytest_command = (
        "        run: uv run pytest --cov=app --cov-report=term-missing "
        "--cov-report=xml"
    )
    if test_source.count(canonical_pytest_command) != 1:
        print("test fixture requires one canonical frozen pytest command", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        shutil.copytree(repo_root / ".github", temp_root / ".github")
        shutil.copy2(repo_root / "docker-bake.hcl", temp_root / "docker-bake.hcl")
        mutated_path = temp_root / "infra" / "cluster" / "main.tf"
        mutated_path.parent.mkdir(parents=True)
        mutated_path.write_text(
            source.replace(executable_arg, comment_only_arg),
            encoding="utf-8",
        )
        traefik_result = run_gate(temp_root)
        mutated_path.write_text(source, encoding="utf-8")
        mutated_lint_path = temp_root / ".github" / "workflows" / "lint.yaml"
        mutated_lint_path.write_text(
            lint_source.replace(canonical_helm_command, unsafe_helm_command),
            encoding="utf-8",
        )
        helm_result = run_gate(temp_root)
        mutated_lint_path.write_text(lint_source, encoding="utf-8")
        mutated_test_path = temp_root / ".github" / "workflows" / "test.yaml"
        mutated_test_path.write_text(
            test_source.replace(canonical_pytest_command, coverage_pytest_command),
            encoding="utf-8",
        )
        pytest_command_result = run_gate(temp_root)
        coverage_upload = (
            "\n\n      - name: Upload matrix coverage\n"
            "        uses: actions/upload-artifact@v4"
        )
        mutated_test_path.write_text(
            test_source.replace(
                canonical_pytest_command,
                canonical_pytest_command + coverage_upload,
            ),
            encoding="utf-8",
        )
        coverage_upload_result = run_gate(temp_root)

    expected_error = "k3d cluster-create command does not disable Traefik"
    if traefik_result.returncode != 1 or expected_error not in traefik_result.stderr:
        print("comment-only Traefik sentinel bypassed the structural gate", file=sys.stderr)
        return 1
    expected_error = "Helm lint workflow must run canonical make lint-chart"
    if helm_result.returncode != 1 or expected_error not in helm_result.stderr:
        print("unsafe Helm wildcard bypassed the structural gate", file=sys.stderr)
        return 1
    expected_error = "Python test matrix must run uv run --frozen pytest"
    if (
        pytest_command_result.returncode != 1
        or expected_error not in pytest_command_result.stderr
    ):
        print("unfrozen coverage command bypassed the structural gate", file=sys.stderr)
        return 1
    expected_error = "Python test matrix must not upload generic coverage artifacts"
    if (
        coverage_upload_result.returncode != 1
        or expected_error not in coverage_upload_result.stderr
    ):
        print("generic coverage upload bypassed the structural gate", file=sys.stderr)
        return 1

    print(
        "comment-only Traefik, unsafe Helm wildcard, unfrozen coverage pytest, "
        "and generic coverage upload are rejected"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
