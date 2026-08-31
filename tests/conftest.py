from __future__ import annotations

import base64
import os

import pytest

os.environ.setdefault("RUNSIGIL_INTERNAL_SERVICE_TOKEN", "test-internal-service-token-000000000001")
os.environ.setdefault("RUNSIGIL_GATEWAY_SERVICE_TOKEN", "test-gateway-service-token-0000000000001")
os.environ.setdefault(
    "RUNSIGIL_DEMO_PROVIDER_SIGNING_KEY",
    "test-provider-signing-key-000000000000001",
)
os.environ.setdefault("RUNSIGIL_ACTION_ENCRYPTION_KEY_B64", base64.b64encode(b"a" * 32).decode())
os.environ.setdefault(
    "RUNSIGIL_EVIDENCE_ED25519_PRIVATE_KEY_B64", base64.b64encode(b"e" * 32).decode()
)
os.environ.setdefault("RUNSIGIL_BOOTSTRAP_API_KEY", "rsk_dev_test_bootstrap_key_000001")

if test_url := os.environ.get("RUNSIGIL_TEST_DATABASE_URL"):
    os.environ["RUNSIGIL_DATABASE_URL"] = test_url
if worker_url := os.environ.get("RUNSIGIL_TEST_WORKER_DATABASE_URL"):
    os.environ["RUNSIGIL_WORKER_DATABASE_URL"] = worker_url
if gateway_url := os.environ.get("RUNSIGIL_TEST_GATEWAY_AUTHORIZATION_DATABASE_URL"):
    os.environ["RUNSIGIL_GATEWAY_AUTHORIZATION_DATABASE_URL"] = gateway_url
if owner_url := os.environ.get("RUNSIGIL_TEST_OWNER_DATABASE_URL"):
    os.environ["RUNSIGIL_MIGRATION_DATABASE_URL"] = owner_url


@pytest.fixture
def database_urls() -> dict[str, str]:
    names = {
        "app": "RUNSIGIL_TEST_DATABASE_URL",
        "worker": "RUNSIGIL_TEST_WORKER_DATABASE_URL",
        "gateway": "RUNSIGIL_TEST_GATEWAY_AUTHORIZATION_DATABASE_URL",
        "owner": "RUNSIGIL_TEST_OWNER_DATABASE_URL",
    }
    values = {name: os.environ.get(variable, "") for name, variable in names.items()}
    if not all(values.values()):
        pytest.skip("isolated RunSigil PostgreSQL URLs are not configured")
    return values


@pytest.fixture
def api_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['RUNSIGIL_BOOTSTRAP_API_KEY']}"}
