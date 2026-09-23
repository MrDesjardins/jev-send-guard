from core import status_client


def test_parse_api_degraded_status():
    page = '<svg aria-label="Degraded"></svg><div>api.typesafe.ai</div>'
    assert status_client._parse_api_issue(page) == "TypeSafe API is degraded"


def test_parse_api_operational_status():
    page = '<svg aria-label="Operational"></svg><div>api.typesafe.ai</div>'
    assert status_client._parse_api_issue(page) is None


def test_unrelated_degradation_is_not_reported_as_a_jev_issue():
    page = (
        '<svg aria-label="Degraded"></svg><div>console.typesafe.ai</div>'
        '<svg aria-label="Operational"></svg><div>api.typesafe.ai</div>'
    )
    assert status_client._parse_api_issue(page) is None
