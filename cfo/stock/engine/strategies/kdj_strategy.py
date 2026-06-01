import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid


"""
https://market-bulls.com/kdj-indicator/#:~:text=The%20KDJ%20trading%20strategy%20primarily,crosses%20below%20the%20%25D%20line.
"""
def create_signals(
    df,
    high=None,
    low=None,
    close=None,
    prefix="",
    suffix="",
    **kwargs,
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length =  kwargs.get("length", 9)
    signal = kwargs.get("signal", 3)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data[["k", "d", "j"]] = df.ta.kdj(
        high=high, low=low, close=close, length=length, signal=signal
    )
    data["p_k"] = data["k"].shift(1)
    data["p_d"] = data["d"].shift(1)
    data["p_j"] = data["j"].shift(1)

    data["CROSSOVER_BULL"] = 0
    data["CROSSOVER_BEAR"] = 0
    data.loc[
        (data["k"] > data["d"])
        & (data["p_k"] < data["p_d"])
        & (data["j"] > data["d"])
        & (data["p_j"] < data["p_d"]),
        "CROSSOVER_BULL",
    ] = 1
    data.loc[
        (data["k"] < data["d"])
        & (data["p_k"] > data["p_d"])
        & (data["j"] < data["d"])
        & (data["p_k"] > data["p_d"]),
        "CROSSOVER_BEAR",
    ] = 1
    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_KDJ_CROSSOVER_BULL{suffix}"] = data[
        "CROSSOVER_BULL"
    ]
    buy_sell_signals[f"{prefix}c_KDJ_CROSSOVER_BEAR{suffix}"] = data[
        "CROSSOVER_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_kdj_signal = "c_KDJ_CROSSOVER_BULL"
kdj_grid_of_parameter = parameter_grid({
    "length": [5, 9, 13, 20],
    "signal": [2, 3, 5, 7],
},
lambda grid: grid["length"] > grid["signal"])
