import pandas as pd
import pandas_ta
import numpy as np
import gc
from strategies.ta_util import over_bought_sold_signal
from parameter_grid import parameter_grid

"""
https://school.stockcharts.com/doku.php?id=technical_indicators:ultimate_oscillator
"""


def create_signals(
    df,
    high,
    low,
    close,
    fast_w=None,
    medium_w=None,
    slow_w=None,
    prefix="",
    suffix="",
    **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    fast = kwargs.get("fast", 7)  # int(fast) if fast and fast > 0 else 7
    fast_w = float(fast_w) if fast_w and fast_w > 0 else 4.0
    medium = kwargs.get("medium", 14)  # int(medium) if medium and medium > 0 else 14
    medium_w = float(medium_w) if medium_w and medium_w > 0 else 2.0
    slow = kwargs.get("slow", 28)  # int(slow) if slow and slow > 0 else 28
    slow_w = float(slow_w) if slow_w and slow_w > 0 else 1.0
    limit_delta = kwargs.get("limit_delta", 20)
    drift = kwargs.get("drift", 1)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["uo"] = df.ta.uo(
        high=high,
        low=low,
        close=close,
        fast=fast,
        medium=medium,
        slow=slow,
        fast_w=fast_w,
        medium_w=medium_w,
        slow_w=slow_w,
        drift=drift,
    )

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
    ] = over_bought_sold_signal(data, "uo", overbought=up_limit, oversold=down_limit)
    # data["p_uo"] = data["uo"].shift(1)

    # # Bullish TSI Signal Line Cross
    # data["BULL_CROSS"] = 0
    # data["BEAR_CROSS"] = 0
    # data.loc[
    #     (data["uo"] > 50) & (data["p_uo"] < 50), "BULL_CROSS"
    # ] = 1
    # data.loc[
    #     (data["uo"] < 50) & (data["p_uo"] > 50), "BEAR_CROSS"
    # ] = 1

    buy_sell_signals = pd.DataFrame(index=df.index)
    # buy_sell_signals[f"{prefix}c_UO_BULL_CROSS{suffix}"] = data["BULL_CROSS"]
    # buy_sell_signals[f"{prefix}c_UO_BEAR_CROSS{suffix}"] = data["BEAR_CROSS"]
    buy_sell_signals[f"{prefix}c_UO_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_UO_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_UO_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_UO_OVERBOUGHT_BULL{suffix}"] = data[
        "OVERBOUGHT_BULL"
    ]
    buy_sell_signals[f"{prefix}c_UO_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_UO_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_UO_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_uo_signal = "c_UO_OVERBOUGHT_BULL"
uo_grid_of_parameter = parameter_grid(
    {
        "fast": [3, 5, 7, 9],
        "medium": [6, 10, 14, 18],
        "slow": [12, 20, 28, 36],
        "limit_delta": [10, 20, 25, 30],
        "drift": [1, 2, 3, 4],
    },
    lambda grid: grid["medium"] == grid["fast"] * 2 and grid["slow"] == grid["medium"] * 2,
)
