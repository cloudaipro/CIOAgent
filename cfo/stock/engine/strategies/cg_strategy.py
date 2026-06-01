import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid
from strategies.ta_util import crossover_signal

"""
https://www.mesasoftware.com/papers/TheCGOscillator.pdf
"""


def create_signals(df, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    length = kwargs.get("length", 10)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["cg"] = df.ta.cg(close=close, length=length)
    data["cg_zero_lag"] = data.ta.zlma(
        close="cg", length=length
    )  # length=int(length / 2))

    data[["CROSSOVER_BULL", "CROSSOVER_BEAR", "CROSSOVER_CSLS"]] = crossover_signal(
        data, "cg", "cg_zero_lag"
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_CG_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_CG_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}f_CG_CROSSOVER_CSLS{suffix}"] = data["CROSSOVER_CSLS"]

    del data
    gc.collect()

    return buy_sell_signals

default_cg_signal = "c_CG_CROSSOVER_BULL"
cg_grid_of_parameter = parameter_grid({
    "length": [3, 5, 7, 10, 14, 20],
})
