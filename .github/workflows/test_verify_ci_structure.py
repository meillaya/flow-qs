#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    source_path = repo_root / "infra" / "cluster" / "main.tf"
    source = source_path.read_text(encoding="utf-8")
    executable_arg = '          --k3s-arg "--disable=traefik@server:*" \\'
    comment_only_arg = '          # --k3s-arg "--disable=traefik@server:*"'
    if source.count(executable_arg) != 1:
        print("test fixture requires one executable Traefik-disable argument", file=sys.stderr)
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
        result = subprocess.run(
            [sys.executable, ".github/workflows/verify_ci_structure.py"],
            cwd=temp_root,
            capture_output=True,
            text=True,
        )

    expected_error = "k3d cluster-create command does not disable Traefik"
    if result.returncode != 1 or expected_error not in result.stderr:
        print("comment-only Traefik sentinel bypassed the structural gate", file=sys.stderr)
        return 1

    print("comment-only Traefik sentinel is rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
