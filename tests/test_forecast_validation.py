"""Forecast validation must not learn from later posts or invent held-out scores."""
from datetime import timedelta
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from app import stats
from app.insights import model as M, validation as V


class ForecastValidation(unittest.TestCase):
    def frame(self, n=120):
        return pd.DataFrame({'id': range(n), 'dt': pd.date_range('2025-01-01', periods=n, tz='UTC'),
                             'chat_id': ['1'] * n, 'score': [100.] * n, 'by_views': [True] * n,
                             'index': [2.] * n})

    def test_norm_ignores_future_and_posts_younger_than_a_week(self):
        frame = self.frame()
        before = V.causal_index(frame, stats.MIN_AGE)
        frame.loc[80:, 'score'] = 100000
        after = V.causal_index(frame, stats.MIN_AGE)
        np.testing.assert_allclose(before.loc[:79, 'index'], after.loc[:79, 'index'], equal_nan=True)
        self.assertEqual(after.loc[86, 'baseline'], 100)
        self.assertTrue(np.isnan(after.loc[8, 'index']))
        self.assertEqual(after.loc[9, 'index'], 1)

    def test_norm_does_not_mix_channels_and_handles_zero_reactions(self):
        first = self.frame(40)
        second = first.assign(id=lambda x: x.id + 40, chat_id='2', score=0., by_views=False)
        result = V.causal_index(pd.concat([first, second]), stats.MIN_AGE)
        mature = result[result['index'].notna()]
        np.testing.assert_allclose(mature['index'], 1)
        self.assertEqual(mature[mature.chat_id == '2']['baseline'].iloc[0], 0)

    def test_forward_windows_keep_maturity_gap_and_never_score_training_rows(self):
        frame = self.frame()
        rows = [{'day': i} for i in range(len(frame))]
        seen = []
        def ridge(X, y, lam):
            seen.append(X[:, 0].copy())
            return np.zeros(X.shape[1]), X.mean(0), 0.
        report, residuals = V.forward_check(frame, rows, [[]] * len(rows), ridge, 4, stats.MIN_AGE)
        self.assertTrue(report['ready'])
        self.assertEqual(report['n'], len(residuals))
        self.assertLess(report['n'], len(frame))
        self.assertAlmostEqual(report['skill'], 0)
        self.assertAlmostEqual(report['typical_err'], 2)
        for window, train_days in zip(report['windows'], seen):
            self.assertLessEqual(pd.Timestamp(window['train_end']) + stats.MIN_AGE, pd.Timestamp(window['test_start']))
            self.assertLess(train_days.max(), (pd.Timestamp(window['test_start']) - frame.dt.iloc[0]).days)

    def test_vocabulary_is_selected_without_held_out_tags(self):
        rows = [{'day': i} for i in range(40)]
        train_tags = [['armor'] if i < 10 else [] for i in range(40)]
        _, names, vocab = V.design(rows, train_tags)
        X, reused_names, _ = V.design([{'future_feature': 1}], [['future_only']], vocab=(names, vocab))
        self.assertEqual(names, reused_names)
        self.assertNotIn('tag:future_only', names)
        self.assertNotIn('future_feature', names)
        self.assertIn('tag:armor', names)
        self.assertFalse(X.any())

    def test_no_holdout_is_reported_as_unchecked_not_perfect(self):
        frame = self.frame(30)
        report, residuals = V.forward_check(frame, [{}] * 30, [[]] * 30, M._ridge, 4, stats.MIN_AGE)
        self.assertFalse(report['ready'])
        self.assertEqual(report['n'], 0)
        self.assertEqual(len(residuals), 0)
        self.assertNotIn('skill', report)

    def test_flat_baseline_has_no_percentage_advantage(self):
        frame = self.frame().assign(index=1.)
        report, _ = V.forward_check(frame, [{'day': i} for i in range(len(frame))], [[]] * len(frame), M._ridge, 4, stats.MIN_AGE)
        self.assertIsNone(report['skill'])
        self.assertEqual(report['baseline_typical_err'], 1)

    def test_full_fit_exposes_forward_quality_and_warns_when_baseline_wins(self):
        frame = self.frame()
        frame['mature'] = True
        frame['ch_rating'] = 'sfw'
        frame['text'] = ''
        frame['media_type'] = 'photo'
        frame['n_media'] = 1
        with patch.object(M, '_history', return_value=frame), patch.object(M, '_all_tags', return_value={}), patch.object(M, '_tagged_ids', return_value=set()):
            fitted = M._fit()
        self.assertTrue(fitted['validation']['ready'])
        self.assertGreater(fitted['sigma'], 0)
        fitted['validation']['skill'] = -.2
        quality = M.quality(fitted)
        self.assertEqual(quality['level'], 'low')
        self.assertIn('не точнее', quality['text'])
        fitted['validation'].update(skill=.25, n=12)
        self.assertEqual(M.quality(fitted)['level'], 'mid')

    def test_forecast_with_no_validation_does_not_invent_numbers(self):
        fitted = {'too_few': False, 'df': self.frame(30), 'n': 30, 'n_tagged': 0,
                  'validation': {'ready': False, 'n': 0}}
        with patch.object(M, 'fit', return_value=fitted), patch.object(M, 'channel_base', return_value={'rating': 'sfw', 'title': 'Fixture'}):
            result = M.forecast(chat_id='1', when=None, kind='single', n_media=1, rating='sfw', text='', tags=None)
        self.assertFalse(result['ok'])
        self.assertNotIn('expected', result)
        self.assertEqual(result['quality']['level'], 'low')
