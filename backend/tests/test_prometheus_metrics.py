from app.metrics.prometheus_metrics import path_group


def test_path_group_collapses_ids():
    assert path_group("/api/trades/12345") == "/api/trades/*"
    assert path_group("/api/health") == "/api/health"
    assert path_group("/metrics") == "/metrics"
