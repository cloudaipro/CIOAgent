import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import over_bought_sold_signal
from parameter_grid import parameter_grid

"""
https://www.tradingsim.com/blog/relative-volatility-index
"""
def create_signals(df, close, high, low, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 14)  # int(length) if length and length > 0 else 14
    drift = kwargs.get("drift", 1)
    limit_delta = kwargs.get("limit_delta", 30)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["rvi"] = df.ta.rvi(close=close, high=high, low=low, length=length, drift=drift)

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
    ] = over_bought_sold_signal(
        data, "rvi", overbought=(50 + limit_delta), oversold=(50 - limit_delta)
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_RVI_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_RVI_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_RVI_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_RVI_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_RVI_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_RVI_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_RVI_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_rvi_signal = "c_RVI_OVERBOUGHT_BULL"
rvi_grid_of_parameter = parameter_grid({
    "length": [5, 9, 14, 20],
    "drift": [1, 2, 3, 4, 5],
    "limit_delta": [10, 20, 25, 30],
})