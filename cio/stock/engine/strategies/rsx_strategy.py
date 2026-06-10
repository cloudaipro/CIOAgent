import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import over_bought_sold_signal, detect_divergence
from parameter_grid import parameter_grid


"""
https://www.tradingview.com/script/fBIe1SWr-STRATEGY-Jurik-RSX/
http://jurikres.com/catalog1/ms_rsx.htm
"""
def create_signals(
    df, close, prefix="", suffix="", **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 14)  # int(length) if length and length > 0 else 14
    ema_length = kwargs.get("ema_length", 9)  # int(ema_length) if ema_length and ema_length > 0 else 9
    limit_delta = kwargs.get("limit_delta", 25)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["rsx"] = df.ta.rsx(close=close, length=length)

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
    ] = over_bought_sold_signal(
        data,
        "rsx",
        overbought=up_limit,
        oversold=down_limit,
        central_line_crossover=True,
    )

    ## divergence
    data["ema"] = df.ta.ema(close=close, length=ema_length)
    data[["DIVERGENCE_BULL", "DIVERGENCE_BEAR"]] = detect_divergence(
        data["ema"], data["rsx"]
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_RSX_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_RSX_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_RSX_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_RSX_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_RSX_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_RSX_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_RSX_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]
    buy_sell_signals[f"{prefix}c_RSX_CENTRALLINE_BULL{suffix}"] = data["CENTRALLINE_BULL"]
    buy_sell_signals[f"{prefix}c_RSX_CENTRALLINE_BEAR{suffix}"] = data["CENTRALLINE_BEAR"]
    buy_sell_signals[f"{prefix}c_RSX_DIVERGENCE_BULL{suffix}"] = data["DIVERGENCE_BULL"]
    buy_sell_signals[f"{prefix}c_RSX_DIVERGENCE_BEAR{suffix}"] = data["DIVERGENCE_BEAR"]
    del data
    gc.collect()

    return buy_sell_signals

default_rsx_signal = "c_RSX_OVERBOUGHT_BULL"
rsx_grid_of_parameter = parameter_grid({
    "length": [5, 9, 14, 20],
    "limit_delta": [10, 20, 25, 30],
})
