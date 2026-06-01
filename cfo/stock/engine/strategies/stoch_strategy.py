import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import over_bought_sold_signal
from parameter_grid import parameter_grid


"""
https://www.tradingview.com/support/solutions/43000502332-stochastic-stoch/
"""
def create_signals(
    df, high, low, close, prefix="", suffix="", **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    k = kwargs.get("k", 14) # k if k and k > 0 else 14
    d = kwargs.get("d", 3)  # d if d and d > 0 else 3
    smooth_k = kwargs.get("smooth_k", 3)  # smooth_k if smooth_k and smooth_k > 0 else 3
    limit_delta = kwargs.get("limit_delta", 30)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data[["k", "d"]] = df.ta.stoch(
        high=high, low=low, close=close, k=k, d=d, smooth_k=smooth_k
    ).iloc[:, :2]

    data[
        [
            "OVERBOUGHT",
            "OVERSOLD",
            "OVERBOUGHTSOLD_CSLS",
            "OVERBOUGHT_BULL",
            "OVERBOUGHT_BEAR",
            "OVERSOLD_BULL",
            "OVERSOLD_BEAR",
        ]
    ] = over_bought_sold_signal(data, "k", overbought=up_limit, oversold=down_limit)

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_STOCH_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_STOCH_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_STOCH_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_STOCH_OVERBOUGHT_BULL{suffix}"] = data[
        "OVERBOUGHT_BULL"
    ]
    buy_sell_signals[f"{prefix}c_STOCH_OVERBOUGHT_BEAR{suffix}"] = data[
        "OVERBOUGHT_BEAR"
    ]
    buy_sell_signals[f"{prefix}c_STOCH_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_STOCH_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_stoch_signal = "c_STOCH_OVERBOUGHT_BULL"
stoch_grid_of_parameter = parameter_grid(
    {
        "k": [5, 9, 14],
        "limit_delta": [10, 20, 25, 30],
    })
