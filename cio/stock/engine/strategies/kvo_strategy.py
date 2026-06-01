import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import (
    find_swings,
    classify_swings,
)
from parameter_grid import parameter_grid


"""
ref:
https://www.daytrading.com/klinger-volume-oscillator
"""
def create_signals(
    df,
    high,
    low,
    close,
    volume,
    prefix="",
    suffix="",
    **kwargs,
):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    fast = kwargs.get("fast", 34)  # int(fast) if fast and fast > 0 else 34
    slow = kwargs.get("slow", 55)  # int(slow) if slow and slow > 0 else 55
    signal = kwargs.get("signal", 13)  # int(signal) if signal and signal > 0 else 13
    cls_limit = kwargs.get("cls_limit", 4)

    sma = df.ta.sma(close=close, length=signal)
    sma_swings = classify_swings(find_swings(sma))

    # Create a DataFrame to store calculated values
    data = df.ta.kvo(
        high=high,
        low=low,
        close=close,
        volume=volume,
        fast=fast,
        slow=slow,
        signal=signal,
    ).dropna()
    price_line = df[close]
    kvo_line = data.iloc[:, 0]
    signal_line = data.iloc[:, 1]
    kvo_signal_swings = classify_swings(
        find_swings(kvo_line - signal_line)
    ).dropna()  # classify_swings(find_swings(kvo_line - signal_line))

    data["CROSSOVER_BULL"] = 0
    data["CROSSOVER_BEAR"] = 0

    data.loc[
        (price_line > sma)
        # (sma_swings["Support"] == True)
        # & (sma_swings["CSLS"] >= 3)
        & (kvo_signal_swings["Support"] == True)
        & (kvo_signal_swings["CSLS"] == cls_limit),
        "CROSSOVER_BULL",
    ] = 1
    data.loc[
        (price_line < sma)
        # (sma_swings["Resistance"] == True)
        # & (sma_swings["CSLS"] >= 3)
        & (kvo_signal_swings["Resistance"] == True)
        & (kvo_signal_swings["CSLS"] == cls_limit),
        "CROSSOVER_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_KVO_CSLS{suffix}"] = kvo_signal_swings["CSLS"] * kvo_signal_swings["Trend"]
    # buy_sell_signals[f"{prefix}c_KVO_SUPPORT{suffix}"] = kvo_signal_swings["Support"]
    # buy_sell_signals[f"{prefix}c_KVO_RESISTANCE{suffix}"] = kvo_signal_swings[
    #     "Resistance"
    # ]
    buy_sell_signals[f"{prefix}c_KVO_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_KVO_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_kvo_signal = "c_KVO_CROSSOVER_BULL"
kvo_grid_of_parameter = parameter_grid(
    {
        "fast": [34, 8, 12],
        "slow": [55, 21, 26],
        "signal": [13, 5, 9],
        "cls_limit": [3, 4, 5],
    },
    lambda grid: (grid["fast"] == 12 and grid["slow"] == 26 and grid["signal"] == 9)
    or (grid["fast"] == 8 and grid["slow"] == 21 and grid["signal"] == 5)
    or (grid["fast"] == 34 and grid["slow"] == 55 and grid["signal"] == 13),
)
