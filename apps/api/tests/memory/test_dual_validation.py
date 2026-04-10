import threading

import pytest


def test_validation_metrics_report():
    """ValidationMetrics.report() returns expected keys."""
    from app.memory.validation_metrics import ValidationMetrics
    m = ValidationMetrics()
    m.record_read(True)
    m.record_read(False)
    m.record_write(True)
    report = m.report()
    assert "reads" in report
    assert report["reads"]["total"] == 2
    assert "divergent" in report["reads"]
    assert report["reads"]["divergent"] == 1
    assert "writes" in report


def test_validation_metrics_thread_safe():
    """Metrics are thread-safe under concurrent access."""
    from app.memory.validation_metrics import ValidationMetrics
    m = ValidationMetrics()

    def hammer():
        for _ in range(100):
            m.record_read(True)
            m.record_write(True)

    threads = [threading.Thread(target=hammer) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m.total_reads == 1000
    assert m.total_writes == 1000
