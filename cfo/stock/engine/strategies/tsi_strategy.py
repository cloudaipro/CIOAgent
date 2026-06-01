import pandas as pd
import pandas_ta
import numpy as np
import gc
from strategies.ta_util import conditional_crossover_signal, over_bought_sold_signal
from parameter_grid import parameter_grid

"""
https://school.stockcharts.com/doku.php?id=technical_indicators:true_strength_index
https://phemex.com/academy/what-is-smi-ergodic-indicator
"""


def create_signals(
    df, close, prefix="", suffix="", **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    fast = kwargs.get("fast", 13)  # int(fast) if fast and fast > 0 else 13
    slow = kwargs.get("slow", 25)  # int(slow) if slow and slow > 0 else 25
    signal = kwargs.get("signal", 13)  # int(signal) if signal and signal > 0 else 13
    drift = kwargs.get("drift", 1)
    limit_delta = kwargs.get("limit_delta", 50)
    up_limit = limit_delta
    down_limit = -limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data[["tsi", "signal"]] = df.ta.tsi(close=close, fast=fast, slow=slow, signal=signal, drift=drift)
    data["p_tsi"] = data["tsi"].shift(1)
    data["p_signal"] = data["signal"].shift(1)

    # Bullish TSI Signal Line Cross
    data["CROSSOVER_BULL"] = 0
    data["CROSSOVER_BEAR"] = 0
    data.loc[
        (data["tsi"] > data["signal"])
        & (data["p_tsi"] < data["p_signal"])
        & (data["tsi"] > 0),
        "CROSSOVER_BULL",
    ] = 1

    data.loc[
        (data["tsi"] < data["signal"])
        & (data["p_tsi"] > data["p_signal"])
        & (data["tsi"] < 0),
        "CROSSOVER_BEAR",
    ] = 1

    data[
        [
            "OVERBOUGHT",
            "OVERSOLD",
            "OVERBOUGHTSOLD_CSLS",
            "OVERBOUGHT_BULL",
            "OVERBOUGHT_BEAR",
            "OVERSOLD_BULL",
            "OVERSOLD_BEAR",
            "CENTRALLINE_BULL",
            "CENTRALLINE_BEAR",
        ]
    ] = over_bought_sold_signal(data, "tsi", overbought=up_limit, oversold=down_limit, central_line_crossover=True)

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_TSI_CROSSOVER_BULL{suffix}"] = data[
        "CROSSOVER_BULL"
    ]
    buy_sell_signals[f"{prefix}c_TSI_CROSSOVER_BEAR{suffix}"] = data[
        "CROSSOVER_BEAR"
    ]
    buy_sell_signals[f"{prefix}c_TSI_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_TSI_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_TSI_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_TSI_OVERBOUGHT_BULL{suffix}"] = data[
        "OVERBOUGHT_BULL"
    ]
    buy_sell_signals[f"{prefix}c_TSI_OVERBOUGHT_BEAR{suffix}"] = data[
        "OVERBOUGHT_BEAR"
    ]
    buy_sell_signals[f"{prefix}c_TSI_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_TSI_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]
    buy_sell_signals[f"{prefix}c_TSI_CENTRALLINE_BULL{suffix}"] = data[
        "CENTRALLINE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_TSI_CENTRALLINE_BEAR{suffix}"] = data[
        "CENTRALLINE_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_tsi_signal = "c_TSI_CROSSOVER_BULL"
tsi_grid_of_parameter = parameter_grid(
    {
        "fast": [5, 9, 13],
        "slow": [10, 18, 25],
        "signal": [3, 7, 9, 13],
        "limit_delta": [20, 30, 50, 60],
    },
    lambda grid: grid["slow"] > grid["fast"] and grid["fast"] > grid["signal"],
)
