from datetime import date, timedelta

from oposiciones_bot.pipeline import collect


class RecordingSource:
    name = "boe"
    def __init__(self, fail=False):
        self.fail = fail
        self.windows = []
    def fetch(self, context):
        self.windows.append(context.lookback_days)
        if self.fail:
            raise RuntimeError("fuente no disponible")
        return []


class DelayedSource(RecordingSource):
    def fetch(self, context):
        self.windows.append(context.lookback_days)
        self.covered_through = context.today - timedelta(days=1)
        self.coverage_warning = (
            f"archivo oficial disponible hasta {self.covered_through.isoformat()}"
        )
        return []


class UnprovenCoverageSource(RecordingSource):
    def fetch(self, context):
        self.windows.append(context.lookback_days)
        self.covered_through = None
        self.coverage_warning = "consulta oficial vacía; cobertura no acreditada"
        return []


class DateOnlyDelayedSource(RecordingSource):
    def fetch(self, context):
        self.windows.append(context.lookback_days)
        self.covered_through = context.today - timedelta(days=1)
        return []


def run_collection(store, app_config, source, today):
    app_config.data["collection"] = {"incremental": True, "lookback_days": 10}
    app_config.data["reminders"] = {"enabled": False}
    return collect(config=app_config, store=store, client=object(), sources=[(source, {})], today=today)


def test_search_from_september_2_to_october_6_inclusive(store, app_config):
    source = RecordingSource()
    run_collection(store, app_config, source, date(2026, 9, 2))
    run_collection(store, app_config, source, date(2026, 10, 6))
    assert source.windows == [10, 34]
    assert store.collection_checkpoint("boe") == date(2026, 10, 6)


def test_failed_source_does_not_advance_checkpoint(store, app_config):
    source = RecordingSource()
    run_collection(store, app_config, source, date(2026, 9, 2))
    source.fail = True
    result = run_collection(store, app_config, source, date(2026, 9, 10))
    assert "boe" in result.errors
    assert store.collection_checkpoint("boe") == date(2026, 9, 2)
    source.fail = False
    run_collection(store, app_config, source, date(2026, 10, 6))
    assert source.windows[-1] == 34


def test_same_day_rechecks_boundary(store, app_config):
    source = RecordingSource()
    run_collection(store, app_config, source, date(2026, 10, 6))
    run_collection(store, app_config, source, date(2026, 10, 6))
    assert source.windows == [10, 0]


def test_checkpoints_are_independent(store, app_config):
    source = RecordingSource()
    run_collection(store, app_config, source, date(2026, 9, 2))
    source.name = "sas"
    run_collection(store, app_config, source, date(2026, 10, 6))
    assert source.windows[-1] == 10
    assert store.collection_checkpoint("boe") == date(2026, 9, 2)


def test_legacy_success_uses_local_calendar_date(store):
    run_id = store.start_source_run("boe")
    store.finish_source_run(run_id, status="OK", now="2026-09-01T23:30:00+00:00")
    assert store.collection_checkpoint("boe") == date(2026, 9, 2)


def test_delayed_official_index_is_partial_and_advances_only_real_coverage(store, app_config):
    source = DelayedSource()
    result = run_collection(store, app_config, source, date(2026, 10, 6))

    assert result.errors == {}
    assert result.warnings == {
        "boe": "archivo oficial disponible hasta 2026-10-05"
    }
    assert store.collection_checkpoint("boe") == date(2026, 10, 5)
    run = store.connection.execute(
        "SELECT status,error FROM source_runs WHERE source='boe' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["status"] == "PARTIAL"
    assert "2026-10-05" in run["error"]


def test_partial_source_without_evidence_does_not_invent_a_checkpoint(store, app_config):
    result = run_collection(
        store, app_config, UnprovenCoverageSource(), date(2026, 10, 6)
    )

    assert "boe" in result.warnings
    assert store.collection_checkpoint("boe") is None


def test_older_coverage_date_is_partial_even_without_adapter_message(store, app_config):
    result = run_collection(
        store, app_config, DateOnlyDelayedSource(), date(2026, 10, 6)
    )

    assert "2026-10-05" in result.warnings["boe"]
    status = store.connection.execute(
        "SELECT status FROM source_runs WHERE source='boe' ORDER BY id DESC LIMIT 1"
    ).fetchone()["status"]
    assert status == "PARTIAL"
