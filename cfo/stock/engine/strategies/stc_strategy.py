import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings, find_data_swings
from strategies.ta_util import over_bought_sold_signal, detect_divergence, crossover_signal
from parameter_grid import parameter_grid

"""
https://howtotrade.com/indicators/schaff-trend-cycle/
"""
def create_signals(
    df,
    close,
    factor=None,
    prefix="",
    suffix="",
    **kwargs,
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    tclength = kwargs.get(
        "tclength", 10
    )  # int(tclength) if tclength and tclength > 0 else 10
    fast = kwargs.get("fast", 12)  # int(fast) if fast and fast > 0 else 12
    slow = kwargs.get("slow", 26)  # int(slow) if slow and slow > 0 else 26
    factor = float(factor) if factor and factor > 0 else 0.5
    limit_delta = kwargs.get("limit_delta", 25)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data[["stc", "macd", "stoch"]] = df.ta.stc(
        close=close, tclength=tclength, fast=fast, slow=slow, factor=factor
    )
    data[["Highs", "Lows", "Last", "Trend"]] = find_data_swings(data["stc"])
    data["25-75_Trend"] = data.loc[
        (data["stc"] > down_limit) & (data["stc"] < up_limit), "Trend"
    ]
    data["25-75_Trend"] = data["25-75_Trend"].fillna(0)
    data["p_25-75_Trend"] = data["25-75_Trend"].shift(1)

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
    ] = over_bought_sold_signal(data, "stc", overbought=up_limit, oversold=down_limit)

    data["ZEROCROSS_BULL"] = 0
    data["ZEROCROSS_BEAR"] = 0
    data.loc[
        (data["25-75_Trend"] > 0) & (data["p_25-75_Trend"] < 0), "ZEROCROSS_BULL"
    ] = 1
    data.loc[
        (data["25-75_Trend"] < 0) & (data["p_25-75_Trend"] > 0), "ZEROCROSS_BEAR"
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_STC_STC{suffix}"] = data["stc"] * 0.01
    buy_sell_signals[f"{prefix}f_STC_STOCH{suffix}"] = data["stoch"] * 0.01
    buy_sell_signals[f"{prefix}c_STC_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_STC_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_STC_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_STC_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_STC_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_STC_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_STC_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]
    buy_sell_signals[f"{prefix}c_STC_ZEROCROSS_BULL{suffix}"] = data["ZEROCROSS_BULL"]
    buy_sell_signals[f"{prefix}c_STC_ZEROCROSS_BEAR{suffix}"] = data["ZEROCROSS_BEAR"]
    del data
    gc.collect()

    return buy_sell_signals

default_stc_signal = "c_STC_OVERBOUGHT_BULL"
stc_grid_of_parameter = parameter_grid(
    {
        "tclength": [5, 10],
        "fast": [3, 8, 12],
        "slow": [10, 17, 21, 26],
        "limit_delta": [10, 20, 25, 30],
    },
    lambda grid: grid["slow"] > grid["fast"] and grid["slow"] > grid["tclength"],
)
