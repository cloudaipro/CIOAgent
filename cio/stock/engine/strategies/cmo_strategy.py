import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import over_bought_sold_signal, crossover_signal
from parameter_grid import parameter_grid


"""
https://www.investopedia.com/terms/c/chandemomentumoscillator.asp
https://trendspider.com/learning-center/chande-momentum-oscillator/
https://www.tradingview.com/script/hdrf0fXV-Variable-Index-Dynamic-Average-VIDYA/
"""
def create_signals(
    df, close, prefix="", suffix="", **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    drift = kwargs.get("drift", 1)
    length = kwargs.get("length", 14)
    sma_length = kwargs.get("sma_length", 10)
    limit_delta = kwargs.get("limit_delta", 50)
    up_limit = limit_delta
    down_limit = -limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["cmo"] = df.ta.cmo(close=close, length=length, drift=drift)
    data["sma"] = data.ta.sma(close="cmo", length=sma_length)

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
    ] = over_bought_sold_signal(data, "cmo", overbought=up_limit, oversold=down_limit)

    data[["CROSSOVER_BULL", "CROSSOVER_BEAR", "CROSSOVER_CSLS"]] = crossover_signal(
        data, "cmo", "sma"
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_CMO_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_CMO_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_CMO_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_CMO_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_CMO_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_CMO_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_CMO_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]
    buy_sell_signals[f"{prefix}c_CMO_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_CMO_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}f_CMO_CROSSOVER_CSLS{suffix}"] = data["CROSSOVER_CSLS"]

    del data
    gc.collect()

    return buy_sell_signals


default_cmo_signal = "c_CMO_OVERBOUGHT_BULL"
cmo_grid_of_parameter = parameter_grid({
    "length": [5, 9, 14, 20],
    "sma_length": [3, 5, 7, 10, 15, 20],
    "limit_delta": [50, 60, 75],
    "drift": [1, 2, 3, 4, 5],
})
