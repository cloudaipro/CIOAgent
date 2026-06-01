import pandas as pd
import pandas_ta
import numpy as np
import gc
from parameter_grid import parameter_grid
from strategies.ta_util import over_bought_sold_signal

'''
https://www.whselfinvest.com/en-be/trading-platform/free-trading-strategies/tradingsystem/33-kaufman-efficiency-ratio
'''
def create_signals(df, close, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    drift = kwargs.get("drift", 1)
    length = kwargs.get("length", 13)
    limit_delta = kwargs.get("limit_delta", 0.1)
    up_limit = 0.5 + limit_delta
    down_limit = 0.5 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["er"] = df.ta.er(close=close, length=length, drift=drift)

    data[
        [
            "UPZONE",
            "DOWNZONE",
            "UPDOWNZONE_CSLS",
            "UPZONE_BULL",
            "UPZONE_BEAR",
            "DOWNZONE_BULL",
            "DOWNZONE_BEAR",
        ]
    ] = over_bought_sold_signal(data, "er", overbought=up_limit, oversold=down_limit)

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_ER_UPZONE{suffix}"] = data["UPZONE"]
    buy_sell_signals[f"{prefix}c_ER_DOWNZONE{suffix}"] = data["DOWNZONE"]
    buy_sell_signals[f"{prefix}f_ER_UPDOWNZONE_CSLS{suffix}"] = data["UPDOWNZONE_CSLS"]
    buy_sell_signals[f"{prefix}c_ER_UPZONE_BULL{suffix}"] = data["UPZONE_BULL"]
    buy_sell_signals[f"{prefix}c_ER_UPZONE_BEAR{suffix}"] = data["UPZONE_BEAR"]
    buy_sell_signals[f"{prefix}c_ER_DOWNZONE_BULL{suffix}"] = data["DOWNZONE_BULL"]
    buy_sell_signals[f"{prefix}c_ER_DOWNZONE_BEAR{suffix}"] = data["DOWNZONE_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_er_signal = "c_ER_UPZONE_BULL"
er_grid_of_parameter = parameter_grid({
    "length": [5, 10, 15, 20],
    # "drift": [1, 2, 3, 4, 5],
    "limit_delta": [0.25, 0.3, 0.35, 0.4],
})
