from datetime import date, timedelta

from desk.limits import Recent, RunBudget


def test_only_so_many_runs_at_once():
    budget = RunBudget(concurrent=2, per_day=10)
    assert budget.start() is None and budget.start() is None
    assert "Try again" in budget.start()
    budget.finish()
    assert budget.start() is None


def test_only_so_many_runs_a_day_and_the_count_resets():
    budget = RunBudget(concurrent=5, per_day=2)
    for _ in range(2):
        assert budget.start() is None
        budget.finish()
    assert "tomorrow" in budget.start()
    budget.day = date.today() - timedelta(days=1)
    assert budget.start() is None


def test_recent_keeps_only_the_newest():
    recent = Recent(2)
    recent["a"], recent["b"], recent["c"] = 1, 2, 3
    assert list(recent) == ["b", "c"]
    recent["b"] = 4  # touched: now the newest
    recent["d"] = 5
    assert list(recent) == ["b", "d"]
