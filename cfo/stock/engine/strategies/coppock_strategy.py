import pandas as pd
import pandas_ta
import gc
from parameter_grid import parameter_grid

"""
https://en.wikipedia.org/wiki/Coppock_curve
"""
def create_signals(
    df, close, prefix="", suffix="", **kwargs
):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 7)
    fast = kwargs.get("fast", 5)
    slow = kwargs.get("slow", 10)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["coppock"] = df.ta.coppock(close=close, length=length, fast=fast, slow=slow)
    data["p_coppock"] = data["coppock"].shift(1)
    data["pp_coppock"] = data["coppock"].shift(2)

    data["COPPOCK_BULL"] = 0
    data.loc[
        (data["coppock"] < 0)
        & (data["coppock"] > data["p_coppock"])
        & (data["p_coppock"] < data["pp_coppock"]),
        "COPPOCK_BULL",
    ] = 1
    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_COPPOCK_BULL{suffix}"] = data["COPPOCK_BULL"]

    del data
    gc.collect()

    return buy_sell_signals

default_coppock_signal = "c_COPPOCK_BULL"
coppock_grid_of_parameter = parameter_grid(
    {
        "length": [3, 5, 9],
        "fast": [3, 5, 7],
        "slow": [6, 10, 14],
    },
    lambda grid: grid["slow"] > grid["fast"],
)
