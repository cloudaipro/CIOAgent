import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid
from strategies.ta_util import over_bought_sold_signal

"""
https://www.oanda.com/bvi-ft/lab-education/technical_analysis/commodity-channel-index/
"""
def create_signals(df, high, low, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    length = kwargs.get("length", 14)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["cci"] = df.ta.cci(high=high, low=low, close=close, length=length)

    data[
        [
            "OVERBOUGHT",
            "OVERSOLD",
            "CSLS",
            "OVERBOUGHT_BULL",
            "OVERBOUGHT_BEAR",
            "OVERSOLD_BULL",
            "OVERSOLD_BEAR",
            "CENTRALLINE_BULL",
            "CENTRALLINE_BEAR",
        ]
    ] = over_bought_sold_signal(
        data,
        "cci",
        overbought=100,
        oversold=-100,
        central_line_crossover=True,
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_CCI_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_CCI_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_CCI_CSLS{suffix}"] = data["CSLS"]
    buy_sell_signals[f"{prefix}c_CCI_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_CCI_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_CCI_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_CCI_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]
    buy_sell_signals[f"{prefix}c_CCI_CENTRALLINE_BULL{suffix}"] = data[
        "CENTRALLINE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_CCI_CENTRALLINE_BEAR{suffix}"] = data[
        "CENTRALLINE_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_cci_signal = "c_CCI_OVERBOUGHT_BULL"
cci_grid_of_parameter = parameter_grid(
    {
    "length": [3, 5, 7, 9, 14, 20],
    }
)
