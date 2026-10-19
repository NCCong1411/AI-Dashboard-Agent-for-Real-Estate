from scripts.benchmark import summarize


def test_nearest_rank_p95_and_failures_cannot_be_hidden_by_successes():
    samples = [{"ok": True, "total_ms": number, "api_ms": number / 2, "db_ms": 1, "render_after_api_ms": 2} for number in range(1, 31)]
    summary = summarize(samples, 3000)
    assert summary["p95_ms"] == 29 and summary["timing_gate_passed"]
    failed = summarize(samples + [{"ok": False}], 3000)
    assert failed["error_count"] == 1 and not failed["timing_gate_passed"]
    assert not summarize(samples[:29], 3000)["timing_gate_passed"]
    assert not summarize(samples, 29)["timing_gate_passed"]
    assert summarize([], 3000)["p95_ms"] is None
