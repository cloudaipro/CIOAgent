import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid


"""
https://stonehillforex.com/2024/02/dorsey-inertia-as-a-confirmation-indicator/
"""
def create_signals(
    df,
    close=None,
    high=None,
    low=None,
    prefix="",
    suffix="",
    **kwargs,
):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 20)
    rvi_length = kwargs.get("rvi_length", 14)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["inertia"] = df.ta.inertia(
        close=close, high=high, low=low, length=length, rvi_length=rvi_length
    )
    data["p_inertia"] = data["inertia"].shift(1)
    data["CENTRALLINE_BULL"] = 0
    data["CENTRALLINE_BEAR"] = 0
    data.loc[(data["inertia"] > 50) & (data["p_inertia"] < 50), "CENTRALLINE_BULL"] = 1
    data.loc[(data["inertia"] < 50) & (data["p_inertia"] > 50), "CENTRALLINE_BEAR"] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_INERTIA_CENTRALLINE_BULL{suffix}"] = data[
        "CENTRALLINE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_INERTIA_CENTRALLINE_BEAR{suffix}"] = data[
        "CENTRALLINE_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_inertia_signal = "c_INERTIA_CENTRALLINE_BULL"
inertia_grid_of_parameter = parameter_grid({
    "length": [5, 10, 15, 20, 30],
    "rvi_length": [5, 9, 14, 20],
})
