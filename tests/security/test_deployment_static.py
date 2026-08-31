from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.security
def test_containers_and_chart_enforce_non_root_read_only_posture() -> None:
    compose = (ROOT / "deploy/compose/compose.yaml").read_text()
    dockerfile = (ROOT / "deploy/compose/Dockerfile.python").read_text()
    helm = (ROOT / "deploy/helm/runsigil/templates/deployments.yaml").read_text()
    assert "USER 65532:65532" in dockerfile
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "runAsNonRoot: true" in helm
    assert "readOnlyRootFilesystem: true" in helm
    assert 'drop: ["ALL"]' in helm


@pytest.mark.security
def test_network_policy_defaults_to_deny_and_limits_provider_port() -> None:
    policy = (ROOT / "deploy/helm/runsigil/templates/networkpolicies.yaml").read_text()
    assert "runsigil-default-deny" in policy
    assert 'policyTypes: ["Ingress", "Egress"]' in policy
    assert "providerCIDR" in policy
    assert "port: 443" in policy


@pytest.mark.security
def test_compose_does_not_broadcast_the_complete_secret_file() -> None:
    compose = (ROOT / "deploy/compose/compose.yaml").read_text()
    assert "env_file:" not in compose
    worker = compose.split("  worker:", 1)[1].split("  web:", 1)[0]
    control_api = compose.split("  control-api:", 1)[1].split("  demo-provider:", 1)[0]
    migrator = compose.split("  migrator:", 1)[1].split("  control-api:", 1)[0]
    assert "RUNSIGIL_MIGRATION_DATABASE_URL" not in worker
    assert "RUNSIGIL_EVIDENCE_ED25519_PRIVATE_KEY_B64" not in control_api
    assert "RUNSIGIL_ACTION_ENCRYPTION_KEY_B64" not in migrator
