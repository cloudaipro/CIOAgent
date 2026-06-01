import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import (
    find_swings,
    classify_swings,
)
from strategies.ta_util import detect_divergence, crossover_signal
from parameter_grid import parameter_grid


"""
https://www.tradingview.com/support/solutions/43000502331-trix/
https://school.stockcharts.com/doku.php?id=technical_indicators:trix
"""
def create_signals(
    df, close, scalar=None, prefix="", suffix="", **kwargs
):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 18)  # int(length) if length and length > 0 else 18
    signal = kwargs.get("signal", 9)  # int(signal) if signal and signal > 0 else 9
    drift = kwargs.get("drift", 1)
    scalar = float(scalar) if scalar else 100

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data[["trix", "signal"]] = df.ta.trix(close=close, length=length, signal=signal, scalar=scalar, drift=drift)

    data[
        ["CROSSOVER_BULL", "CROSSOVER_BEAR", "CROSSOVER_CSLS", "ZEROCROSSING_BULL", "ZEROCROSSING_BEAR"]
    ] = crossover_signal(data, "trix", "signal", zero_line_crossover=True)

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_TRIX_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_TRIX_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]

    buy_sell_signals[f"{prefix}c_TRIX_ZEROCROSSING_BULL{suffix}"] = data["ZEROCROSSING_BULL"]
    buy_sell_signals[f"{prefix}c_TRIX_ZEROCROSSING_BEAR{suffix}"] = data["ZEROCROSSING_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_trix_signal = "c_TRIX_CROSSOVER_BULL"
trix_grid_of_parameter = parameter_grid(
    {
        "length": [6, 10, 14, 18, 20],
        "signal": [3, 5, 9],
    },
    lambda grid: grid["length"] > grid["signal"],
)
