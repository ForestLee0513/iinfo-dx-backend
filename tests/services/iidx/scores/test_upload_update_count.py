import unittest

from app.services.iidx.scores.updates import classify_chart_score_changes


def _score(title: str, difficulty: str, **values):
    return {"title": title, "difficulty": difficulty, **values}


class ScoreUpdateCountTests(unittest.TestCase):
    def test_first_snapshot_counts_every_chart_as_added(self):
        changes = classify_chart_score_changes(
            [], [_score("A", "HYPER", ex_score=100), _score("B", "ANOTHER", ex_score=200)]
        )
        self.assertEqual(changes.added, 2)
        self.assertEqual(changes.updated, 0)

    def test_does_not_count_an_unchanged_chart(self):
        score = _score("A", "HYPER", ex_score=100, clear_type="CLEAR")
        self.assertEqual(classify_chart_score_changes([score], [score.copy()]).total, 0)

    def test_counts_changed_and_new_charts_but_not_missing_charts(self):
        previous = [
            _score("A", "HYPER", ex_score=100, clear_type="CLEAR"),
            _score("B", "ANOTHER", ex_score=200),
        ]
        current = [
            _score("A", "HYPER", ex_score=101, clear_type="CLEAR"),
            _score("C", "HYPER", ex_score=300),
        ]

        changes = classify_chart_score_changes(previous, current)
        self.assertEqual(changes.added, 1)
        self.assertEqual(changes.updated, 1)
        self.assertEqual(changes.total, 2)
