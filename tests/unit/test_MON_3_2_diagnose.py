"""MON-3.2 (ADR-0020 part B, K5, OC-12): the three first rules on simulated windows."""

from stratal.mon.diagnose import Observed, diagnose, expected, observe


def _seen(
    gpu: float, cores: float | None, *, workers: int = 0, disk: float = 1.0, n: int = 30
) -> Observed:
    return observe(
        gpu=[gpu] * n,
        disk_read=[disk] * n,
        cpu_cores=[cores] * n,
        loader_workers=workers,
        logical_cpus=16,
    )


def _diagnose(seen: Observed, *, queue: str = "gpu", running: float = 120.0) -> str | None:
    found = diagnose(seen, queue=queue, running_for_s=running, disk_threshold_mb_s=50.0)
    return None if found is None else found.code


def test_MON_3_2_a_slow_loader_is_data_loading() -> None:
    seen = _seen(12.0, 0.97)
    found = diagnose(seen, queue="gpu", running_for_s=120.0, disk_threshold_mb_s=50.0)
    assert found is not None and found.code == "diag_data_loading"
    assert "Data loading is the bottleneck" in found.message
    assert "Increase DataLoader workers (now 0)" in found.action


def test_MON_3_2_capacity_counts_the_loader_workers() -> None:
    assert _seen(12.0, 0.97, workers=3).capacity_cores == 4
    assert _diagnose(_seen(12.0, 0.97, workers=3)) == "diag_gpu_waiting", "1 of 4 cores"
    assert _diagnose(_seen(12.0, 3.6, workers=3)) == "diag_data_loading"
    assert (
        observe(
            gpu=[1.0], disk_read=[0.0], cpu_cores=[1.0], loader_workers=40, logical_cpus=16
        ).capacity_cores
        == 16
    )


def test_MON_3_2_disk_then_everything_low() -> None:
    assert _diagnose(_seen(18.0, 0.3, disk=180.0)) == "diag_disk"
    assert _diagnose(_seen(20.0, 0.4, disk=4.0)) == "diag_gpu_waiting"


def test_MON_3_2_nothing_when_the_gpu_is_busy_or_too_soon_or_too_few_samples() -> None:
    assert _diagnose(_seen(30.0, 0.97)) is None, "30 % or more is busy enough"
    assert _diagnose(_seen(12.0, 0.97), running=45.0) is None, "warm-up"
    assert _diagnose(_seen(12.0, 0.97, n=10)) is None, "too few samples"
    assert _diagnose(_seen(12.0, 0.97), queue="cpu") is None, "rules are for GPU stages"


def test_MON_3_2_unknown_job_cpu_is_never_called_saturated() -> None:
    assert _diagnose(_seen(12.0, None)) == "diag_gpu_waiting"


def test_MON_3_2_expected_resource_follows_the_mon_3_table() -> None:
    assert expected("label", "gpu") == ("gpu",)
    assert expected("train", "gpu") == ("gpu",)
    assert expected("train", "cpu") == ("cpu",), "tiny models train on the CPU"
    assert expected("ingest", "cpu") == ("disk", "cpu")
    assert expected("clean", "cpu") == ("cpu", "ram")
