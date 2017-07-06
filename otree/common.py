"""oTree Public API utilities"""

import json
from django.conf import settings
from django.utils.safestring import mark_safe
from otree import easymoney

# these are needed because otree.common was part of our old public API
from otree.easymoney import Currency, RealWorldCurrency



class _CurrencyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, easymoney.Currency):
            return float(obj)
        # Let the base class default method raise the TypeError
        return json.JSONEncoder.default(self, obj)


def safe_json(obj):
    return mark_safe(json.dumps(obj, cls=_CurrencyEncoder))


def currency_range(first, last, increment):
    assert last >= first
    if Currency(increment) == 0:
        if settings.USE_POINTS:
            setting_name = 'POINTS_DECIMAL_PLACES'
        else:
            setting_name = 'REAL_WORLD_CURRENCY_DECIMAL_PLACES'
        raise ValueError(
            ('currency_range() step argument must not be zero. '
             'Maybe your {} setting is '
             'causing it to be rounded to 0.').format(setting_name)
        )

    assert increment > 0  # not negative

    values = []
    current_value = Currency(first)

    while True:
        if current_value > last:
            return values
        values.append(current_value)
        current_value += increment
