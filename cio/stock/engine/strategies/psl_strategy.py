import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import over_bought_sold_signal
from parameter_grid import parameter_grid

"""
https://help.tradestation.com/10_00/eng/tradestationhelp/elanalysis/indicator/psychological_line_indicator_.htm
"""
def create_signals(df, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = length = kwargs.get("length", 12) # int(length) if length and length > 0 else 12
    limit_delta = kwargs.get("limit_delta", 25)
    drift = kwargs.get("drift", 1)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["psl"] = df.ta.psl(close=close, length=length, drift=drift)
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
    ] = over_bought_sold_signal(data, "psl", overbought=up_limit, oversold=down_limit)

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_PSL_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_PSL_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_PSL_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_PSL_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_PSL_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_PSL_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_PSL_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_psl_signal = "c_PSL_OVERBOUGHT_BULL"
psl_grid_of_parameter = parameter_grid({
    "length": [5, 9, 12, 15, 20],
    "limit_delta": [10, 20, 25, 30],
    "drift": [1, 2, 3, 4, 5],
})
