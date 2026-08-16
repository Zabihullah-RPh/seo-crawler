from app.integrations.common_crawl_discovery import (
    BacklinkDiscoveryConfig,
    build_discovery_plan,
    discovery_info,
    to_reverse_domain,
)


def test_reverse_domain():
    assert to_reverse_domain("https://www.example.com/") == "com.example.www"


def test_build_plan_is_download_free():
    plan = build_discovery_plan("https://www.example.com/")
    assert plan.target_domain == "www.example.com"
    assert plan.reverse_domain == "com.example.www"
    assert plan.data_download_enabled is False
    assert plan.mode == "planned"


def test_custom_graph_config():
    plan = build_discovery_plan(
        "example.com",
        BacklinkDiscoveryConfig(graph_release="cc-main-test", graph_level="host"),
    )
    assert plan.graph_release == "cc-main-test"
    assert plan.graph_level == "host"


def test_discovery_info_makes_no_network_request():
    info = discovery_info()
    assert info["phase"] == "discovery-planning"
    assert info["data_download_enabled"] is False
    assert info["network_requests_enabled"] is False
