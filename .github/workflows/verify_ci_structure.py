#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_SERVICES = frozenset(
    {
        "cart-service",
        "catalog-service",
        "checkout-service",
        "incident-engine",
        "inventory-service",
        "order-service",
        "synthetic-monitor",
    }
)
IMAGE_SERVICES = PYTHON_SERVICES | {"payment-gateway"}
K3D_RESOURCE = re.compile(r'^\s*resource\s+"null_resource"\s+"k3d_cluster"\s*\{')
NEXT_RESOURCE = re.compile(r"^\s*resource\s+")
CLUSTER_CREATE = re.compile(
    r"^\s*\$\{local\.k3d_bin\}\s+cluster\s+create\s+\$\{var\.cluster_name\}\s+\\$"
)
COMMAND_HEREDOC = re.compile(r"^\s*command\s*=\s*<<-?([A-Z][A-Z0-9_]*)\s*$")


def workflow_services(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    service_marker = next(index for index, line in enumerate(lines) if line == "        service:")
    services: set[str] = set()
    for line in lines[service_marker + 1 :]:
        if line.startswith("          - "):
            services.add(line.removeprefix("          - "))
        elif line.strip():
            break
    return services


def bake_services() -> tuple[set[str], set[str]]:
    rendered = subprocess.run(
        ["docker", "buildx", "bake", "-f", "docker-bake.hcl", "--print"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    bake = json.loads(rendered.stdout)
    return set(bake["group"]["default"]["targets"]), set(bake["target"])


def require_exact(name: str, actual: set[str], expected: frozenset[str]) -> list[str]:
    failures = []
    if missing := sorted(expected - actual):
        failures.append(f"{name} is missing: {', '.join(missing)}")
    if unexpected := sorted(actual - expected):
        failures.append(f"{name} has unexpected entries: {', '.join(unexpected)}")
    return failures


def command_bodies(resource_lines: list[str]) -> list[list[str]]:
    bodies = []
    index = 0
    while index < len(resource_lines):
        match = COMMAND_HEREDOC.fullmatch(resource_lines[index])
        if match is None:
            index += 1
            continue
        terminator = match.group(1)
        body_start = index + 1
        body_end = next(
            (
                line_index
                for line_index in range(body_start, len(resource_lines))
                if resource_lines[line_index].strip() == terminator
            ),
            len(resource_lines),
        )
        bodies.append(resource_lines[body_start:body_end])
        index = body_end + 1
    return bodies


def infra_guardrail_failures() -> list[str]:
    lines = (REPO_ROOT / "infra" / "cluster" / "main.tf").read_text(
        encoding="utf-8"
    ).splitlines()
    resource_start = next(
        (index for index, line in enumerate(lines) if K3D_RESOURCE.fullmatch(line)),
        None,
    )
    if resource_start is None:
        return ["k3d cluster resource is missing"]
    resource_end = next(
        (
            index
            for index, line in enumerate(lines[resource_start + 1 :], resource_start + 1)
            if NEXT_RESOURCE.match(line)
        ),
        len(lines),
    )
    resource_lines = lines[resource_start:resource_end]
    cluster_commands = [
        (body, index)
        for body in command_bodies(resource_lines)
        for index, line in enumerate(body)
        if CLUSTER_CREATE.fullmatch(line)
    ]
    if not cluster_commands:
        return ["k3d cluster-create command is missing"]
    if len(cluster_commands) != 1:
        return ["k3d cluster resource has multiple cluster-create commands"]
    command_body, command_start = cluster_commands[0]

    argument_lines = []
    for line in command_body[command_start + 1 :]:
        stripped = line.strip()
        if not stripped:
            break
        continued = stripped.endswith("\\")
        argument_lines.append(stripped.removesuffix("\\").rstrip())
        if not continued:
            break

    required_tokens = ["--k3s-arg", "--disable=traefik@server:*"]
    if sum(shlex.split(line) == required_tokens for line in argument_lines) != 1:
        return ["k3d cluster-create command does not disable Traefik"]
    return []


def main() -> int:
    failures = []
    for workflow_name, expected in (
        ("lint.yaml", PYTHON_SERVICES),
        ("test.yaml", PYTHON_SERVICES),
        ("build-images.yaml", IMAGE_SERVICES),
    ):
        actual = workflow_services(REPO_ROOT / ".github" / "workflows" / workflow_name)
        failures.extend(require_exact(workflow_name, actual, expected))

    default_targets, declared_targets = bake_services()
    failures.extend(require_exact("Bake default group", default_targets, IMAGE_SERVICES))
    failures.extend(require_exact("Bake targets", declared_targets, IMAGE_SERVICES))
    failures.extend(infra_guardrail_failures())

    if failures:
        print("service coverage verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "CI structure verified: lint/test=7 Python services; "
        "build/Bake=8 images; Traefik disabled"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
