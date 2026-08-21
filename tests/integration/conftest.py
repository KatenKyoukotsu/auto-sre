import os

import httpx
import pytest

SRE_AGENT_URL = os.environ.get("SRE_AGENT_URL", "http://localhost:8096")
ALERT_ANALYZER_URL = os.environ.get("ALERT_ANALYZER_URL", "http://localhost:8097")


def _reachable(url: str) -> bool:
    try:
        return httpx.get(f"{url}/api/health", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="session")
def sre_agent_url():
    if not _reachable(SRE_AGENT_URL):
        pytest.skip("sre-agent недоступен — стек не поднят")
    return SRE_AGENT_URL


@pytest.fixture(scope="session")
def analyzer_url():
    if not _reachable(ALERT_ANALYZER_URL):
        pytest.skip("alert-analyzer недоступен — стек не поднят")
    return ALERT_ANALYZER_URL
