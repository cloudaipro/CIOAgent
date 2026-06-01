import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid
from strategies.ta_util import crossover_signal

"""
https://www.fmlabs.com/reference/default.htm?url=ForecastOscillator.htm
"""
def create_signals(df, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    length = kwargs.get("length", 9)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["cfo"] = df.ta.cfo(close=close, length=length)
    data["sma"] = data.ta.sma(close="cfo", length=length)

    data[
        [
            "CROSSOVER_BULL",
            "CROSSOVER_BEAR",
            "CROSSOVER_CSLS",
            "ZEROCROSSING_BULL",
            "ZEROCROSSING_BEAR",
        ]
    ] = crossover_signal(data, "cfo", "sma", zero_line_crossover=True)

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_CFO_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_CFO_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}c_CFO_ZEROCROSSING_BULL{suffix}"] = data[
        "ZEROCROSSING_BULL"
    ]
    buy_sell_signals[f"{prefix}c_CFO_ZEROCROSSING_BEAR{suffix}"] = data["ZEROCROSSING_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_cfo_signal = "c_CFO_CROSSOVER_BULL"
cfo_grid_of_parameter = parameter_grid({
    "length": [3, 5, 7, 9, 14, 20],
})
