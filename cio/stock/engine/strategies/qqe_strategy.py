import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import (
    over_bought_sold_signal,
    detect_divergence,
    conditional_crossover_signal,
)
from parameter_grid import parameter_grid


"""
https://howtotrade.com/indicators/qqe-indicator/
https://fxcodebase.com/code/viewtopic.php?f=38&t=63956&p=108514#p108514
"""
def create_signals(df, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 14) # int(length) if length and length > 0 else 14
    smooth = kwargs.get("smooth", 5)  # int(smooth) if smooth and smooth > 0 else 5
    ema_length = kwargs.get("ema_length", 9) # int(length) if length and length > 0 else 9
    drift = kwargs.get("drift", 1)
    limit_delta = kwargs.get("limit_delta", 20)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    fast_factor = 2.618
    slow_factor = 4.236

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data[["fast_line", "rsima"]] = df.ta.qqe(
        close=close, length=length, smooth=smooth, factor=fast_factor, drift=drift
    ).iloc[:, 0:2]
    data["slow_line"] = df.ta.qqe(
        close=close, length=length, smooth=smooth, factor=slow_factor
    ).iloc[:, 0:1]

    ## 1. Bullish and Bearish Divergence Signals
    data["ema"] = df.ta.ema(close=close, length=ema_length)
    data[["DIVERGENCE_BULL", "DIVERGENCE_BEAR"]] = detect_divergence(
        data["ema"], data["rsima"]
    )

    ## 2. Trend Confirmation
    data["TREND_UP"] = 0
    data["TREND_DOWN"] = 0
    data.loc[
        data["rsima"] > 50,
        "TREND_UP",
    ] = 1
    data.loc[
        data["rsima"] < 50,
        "TREND_DOWN",
    ] = 1

    ## 3. Overbought and Oversold Levels
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
    ] = over_bought_sold_signal(data, "rsima", overbought=up_limit, oversold=down_limit)

    ## 4. Trend Trading
    data[["CROSSOVER_BULL", "CROSSOVER_BEAR"]] = conditional_crossover_signal(
        data, "fast_line", "slow_line", 50
    )
    # data[["BULL_TRADING_LINE", "BEAR_TRADING_LINE"]] = conditional_crossover_signal(
    #     data, "fast_line", "slow_line", 50
    # )

    ## Trade Setups on the QQE
    data["RSIMA_BULL"] = 0
    data["RSIMA_BEAR"] = 0
    data.loc[
        (data["rsima"] > data["slow_line"])
        & (data["rsima"].shift(1) < data["slow_line"].shift(1))
        & (data["rsima"] < 50)
        & (data["slow_line"] < 50)
        & (data["fast_line"] < 50),
        "RSIMA_BULL",
    ] = 1
    data.loc[
        (data["rsima"] < data["slow_line"])
        & (data["rsima"].shift(1) > data["slow_line"].shift(1))
        & (data["rsima"] > 50)
        & (data["slow_line"] > 50)
        & (data["fast_line"] > 50),
        "RSIMA_BEAR",
    ] = 1

    ## Confirming Your Trade Setups
    data["RSIMACONFIRM_BULL"] = 0
    data["RSIMACONFIRM_BEAR"] = 0
    data.loc[
        (data["rsima"] > data["slow_line"])
        & (data["rsima"].shift(1) < data["slow_line"].shift(1))
        & (data["rsima"] < 50)
        & (data["slow_line"] < 50)
        & (data["fast_line"] < 50)
        & (data["ema"] < data["close"]),
        "RSIMACONFIRM_BULL",
    ] = 1
    data.loc[
        (data["rsima"] < data["slow_line"])
        & (data["rsima"].shift(1) > data["slow_line"].shift(1))
        & (data["rsima"] > 50)
        & (data["slow_line"] > 50)
        & (data["fast_line"] > 50)
        & (data["ema"] > data["close"]),
        "RSIMACONFIRM_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_QQE_RSIMA{suffix}"] = data["rsima"]
    buy_sell_signals[f"{prefix}f_QQE_FAST{suffix}"] = data["fast_line"]
    buy_sell_signals[f"{prefix}f_QQE_SLOW{suffix}"] = data["slow_line"]
    buy_sell_signals[f"{prefix}c_QQE_DIVERGENCE_BULL{suffix}"] = data["DIVERGENCE_BULL"]
    buy_sell_signals[f"{prefix}c_QQE_DIVERGENCE_BEAR{suffix}"] = data["DIVERGENCE_BEAR"]
    buy_sell_signals[f"{prefix}c_QQE_TREND_UP{suffix}"] = data["TREND_UP"]
    buy_sell_signals[f"{prefix}c_QQE_TREND_DOWN{suffix}"] = data["TREND_DOWN"]
    buy_sell_signals[f"{prefix}c_QQE_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_QQE_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_QQE_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_QQE_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_QQE_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_QQE_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_QQE_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]

    # buy_sell_signals[f"{prefix}c_QQE_CROSSOVER_BULL{suffix}"] = data[
    #     "CROSSOVER_BULL"
    # ]
    # buy_sell_signals[f"{prefix}c_QQE_CROSSOVER_BEAR{suffix}"] = data[
    #     "CROSSOVER_BEAR"
    # ]
    buy_sell_signals[f"{prefix}c_QQE_RSIMA_BULL{suffix}"] = data["RSIMA_BULL"]
    buy_sell_signals[f"{prefix}c_QQE_RSIMA_BEAR{suffix}"] = data["RSIMA_BEAR"]
    buy_sell_signals[f"{prefix}c_QQE_RSIMACONFIRM_BULL{suffix}"] = data[
        "RSIMACONFIRM_BULL"
    ]
    buy_sell_signals[f"{prefix}c_QQE_RSIMACONFIRM_BEAR{suffix}"] = data[
        "RSIMACONFIRM_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_qqe_signal = "c_QQE_OVERBOUGHT_BULL"
qqe_grid_of_parameter = parameter_grid({
        "length": [5, 9, 14, 20],
        "smooth": [3, 5, 7],
        "drift": [1, 2, 3],
        "limit_delta": [10, 20, 25, 30],
    },
    lambda grid: grid["length"] > grid["smooth"]
)
