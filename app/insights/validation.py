"""Retrospective, forward-only evaluation of publication forecasts.

Historical counters are the currently stored totals, not snapshots of what was
known on the prediction date. This is not a replay of saved live forecasts.
"""
import math
from datetime import timedelta

import numpy as np
import pandas as pd


MIN_TRAIN = 30
MIN_TEST = 10


def causal_index(frame, maturity):
    """Normalise each post using only earlier posts already old enough at its date."""
    frame = frame.sort_values('dt', kind='stable').reset_index(drop=True).copy()
    frame['baseline'] = np.nan
    frame['index'] = np.nan
    keys = ['platform', 'chat_id'] if 'platform' in frame else ['chat_id']
    for _, group in frame.groupby(keys):
        previous = group[np.isfinite(group['score']) & (group['score'] >= 0)]
        times = pd.DatetimeIndex(previous['dt'])
        scores = previous['score'].to_numpy(dtype=float)
        for row in group.itertuples():
            end = times.searchsorted(row.dt - maturity, side='right')
            start = times.searchsorted(row.dt - timedelta(days=42), side='left')
            use = scores[start:end] if end - start >= 3 else scores[max(0, end - 15):end]
            if len(use) < 3:
                continue
            base = float(np.median(use))
            offset = 0 if row.by_views else 1
            if base + offset <= 0 or not math.isfinite(row.score) or row.score < 0:
                continue
            index = (row.score + offset) / (base + offset)
            if index > 0:
                frame.loc[row.Index, ['baseline', 'index']] = [base, index]
    return frame


def design(rows, tag_lists, *, vocab=None):
    """Select vocabulary from training rows only; return its reusable encoding."""
    if vocab is None:
        tagged = sum('untagged' not in row for row in rows)
        vocab = []
        if tagged >= 20:
            counts = pd.Series([t for tags in tag_lists for t in set(tags)], dtype=object).value_counts()
            low, high = max(5, int(.015 * tagged)), .7 * tagged
            vocab = [t for t, count in counts.items() if low <= count <= high][:400]
        names = sorted({key for row in rows for key in row} | {'tag:' + tag for tag in vocab})
    else:
        names, vocab = vocab
    idx = {name: i for i, name in enumerate(names)}
    matrix = np.zeros((len(rows), len(names)))
    for i, (row, tags) in enumerate(zip(rows, tag_lists)):
        for key, value in row.items():
            if key in idx:
                matrix[i, idx[key]] = value
        for tag in tags:
            key = 'tag:' + tag
            if key in idx:
                matrix[i, idx[key]] = 1
    return matrix, names, vocab


def forward_check(frame, rows, tag_lists, ridge, lam, maturity):
    """Expanding training windows with a maturity embargo; no shuffled holdouts."""
    y = np.log(frame['index'].to_numpy(dtype=float))
    dates = frame['dt']
    unique_dates = dates.drop_duplicates().sort_values().tolist()
    starts = sorted(set(int(len(unique_dates) * q) for q in (.6, .75, .9)))
    predictions = np.full(len(y), np.nan)
    windows = []
    for i, start in enumerate(starts):
        if start >= len(unique_dates):
            continue
        first = unique_dates[start]
        end = unique_dates[starts[i + 1]] if i + 1 < len(starts) else dates.max() + pd.Timedelta(nanoseconds=1)
        train = np.flatnonzero((dates <= first - maturity).to_numpy())
        test = np.flatnonzero(((dates >= first) & (dates < end)).to_numpy())
        if len(train) < MIN_TRAIN or not len(test):
            continue
        X, names, vocab = design([rows[j] for j in train], [tag_lists[j] for j in train])
        Xt, _, _ = design([rows[j] for j in test], [tag_lists[j] for j in test], vocab=(names, vocab))
        beta, xm, ym = ridge(X, y[train], lam)
        predictions[test] = ym + (Xt - xm) @ beta
        windows.append({'train_n': len(train), 'test_n': len(test),
                        'train_end': dates.iloc[train].max().isoformat(),
                        'test_start': dates.iloc[test].min().isoformat(),
                        'test_end': dates.iloc[test].max().isoformat()})
    tested = np.flatnonzero(np.isfinite(predictions))
    result = {'method': 'forward', 'ready': len(tested) >= MIN_TEST, 'n': len(tested), 'minimum': MIN_TEST,
              'windows': windows, 'embargo_days': maturity.total_seconds() / 86400,
              'start': dates.iloc[tested].min().isoformat() if len(tested) else None,
              'end': dates.iloc[tested].max().isoformat() if len(tested) else None}
    if not len(tested):
        return result, np.array([])
    actual, predicted = y[tested], predictions[tested]
    residuals = actual - predicted
    # A forecast of index=1 is the per-channel historical norm, not a random guess.
    model_error = float(np.mean(np.abs(residuals)))
    baseline_error = float(np.mean(np.abs(actual)))
    result.update(mae_log=model_error, baseline_mae_log=baseline_error,
                  skill=1 - model_error / baseline_error if baseline_error > 1e-9 else None,
                  typical_err=float(np.exp(np.median(np.abs(residuals)))),
                  baseline_typical_err=float(np.exp(np.median(np.abs(actual)))),
                  direction=float(np.mean((predicted > 0) == (actual > 0))))
    return result, residuals
