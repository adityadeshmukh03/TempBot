from __future__ import annotations


def split_walk_forward(rows: list, train_size: int, validate_size: int):
    """Yield rolling train/validation windows without lookahead."""
    start = 0
    while start + train_size + validate_size <= len(rows):
        train = rows[start:start + train_size]
        validate = rows[start + train_size:start + train_size + validate_size]
        yield train, validate
        start += validate_size
