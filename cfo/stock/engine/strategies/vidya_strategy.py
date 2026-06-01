import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid

"""
https://www.perfecttrendsystem.com/blog_mt4_2/en/vidya-indicator-for-mt4
https://www.tradingview.com/script/hdrf0fXV-Variable-Index-Dynamic-Average-VIDYA/
"""
def create_signals(df, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 14)  # int(length) if length and length > 0 else 14
    drift = kwargs.get("drift", 1)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["vidya"] = df.ta.vidya(close=close, length=length, drift=drift)
    data[["Trend", "CSLS"]] = classify_swings(find_swings(data["vidya"]))[["Trend", "CSLS"]]
    data["level"] = data["Trend"] * data["CSLS"]

    data["SUPPORT_BULL"] = 0
    data["SUPPORT_BEAR"] = 0
    data.loc[(data["level"] == 1.0), "SUPPORT_BULL"] = 1
    data.loc[(data["level"] == -1.0), "SUPPORT_BEAR"] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_LEVEL{suffix}"] = data["level"]
    buy_sell_signals[f"{prefix}c_VIDYA_SUPPORT_BULL{suffix}"] = data["SUPPORT_BULL"]
    buy_sell_signals[f"{prefix}c_VIDYA_SUPPORT_BEAR{suffix}"] = data["SUPPORT_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_vidya_signal = "c_VIDYA_SUPPORT_BULL"
vidya_grid_of_parameter = parameter_grid({
        "length": [5, 9, 12, 15, 20],
        "drift": [1, 2, 3, 4, 5],
    }
)
