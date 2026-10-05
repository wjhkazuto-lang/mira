from mira.store import Store


def test_proactive_log_roundtrip(store, clock):
    assert store.last_proactive_at() is None
    store.add_proactive_log("commitment", "commitment", 7)
    assert store.last_proactive_at() == clock.now()
    assert store.last_proactive_ref("commitment", 7) == clock.now()
    assert store.last_proactive_ref("commitment", 8) is None
    assert store.last_proactive_ref("goal", 7) is None


def test_last_proactive_ref_picks_latest(store, clock):
    store.add_proactive_log("commitment", "commitment", 7)
    clock.advance(86400)
    store.add_proactive_log("missing")
    store.add_proactive_log("commitment", "commitment", 7)
    assert store.last_proactive_ref("commitment", 7) == clock.now()


def test_last_proactive_at_includes_letter(store, clock):
    store.add_proactive_log("weekly_letter")
    assert store.last_proactive_at() == clock.now()


def test_count_excludes_weekly_letter_and_other_days(store, clock):
    store.add_proactive_log("missing")
    store.add_proactive_log("weekly_letter")
    assert store.proactive_count_on(clock.now().date()) == 1
    clock.advance(86400)
    assert store.proactive_count_on(clock.now().date()) == 0


def test_opens_existing_db_and_adds_proactive_log(tmp_path, clock):
    first = Store(tmp_path / "m.db", now=clock.now)
    first.add_message("user", "hi")
    second = Store(tmp_path / "m.db", now=clock.now)
    second.add_proactive_log("missing")
    assert second.last_proactive_at() is not None
