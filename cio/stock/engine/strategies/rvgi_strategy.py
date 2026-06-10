import pandas as pd
import pandas_ta
import numpy as np
import gc
from strategies.ta_util import crossover_signal
from parameter_grid import parameter_grid


"""
reef: https://www.investopedia.com/terms/r/relative_vigor_index.asp
"""
def create_signals(
    df,
    open,
    high,
    low,
    close,    
    prefix="",
    suffix="",
    **kwargs
):
    length = kwargs.get("length", 14)  # int(length) if length and length > 0 else 14
    swma_length = kwargs.get(
        "swma_length", 4
    )  # int(swma_length) if swma_length and swma_length > 0 else 4

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data[["rvgi", "signal"]] = df.ta.rvgi(
        open=open,
        high=high,
        low=low,
        close=close,
        length=length,
        swma_length=swma_length,
    )
    data[["CROSSOVER_BULL", "CROSSOVER_BEAR", "CROSSOVER_CSLS"]] = crossover_signal(
        data, "rvgi", "signal"
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_RVGI_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_RVGI_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}f_RVGI_CROSSOVER_CSLS{suffix}"] = data["CROSSOVER_CSLS"]

    del data
    gc.collect()

    return buy_sell_signals

default_rvgi_signal = "c_RVGI_CROSSOVER_BULL"
rvgi_grid_of_parameter = parameter_grid(
    {
        "length": [5, 7, 14, 20],
        "swma_length": [2, 3, 4, 7],
    },
    lambda grid: grid["length"] > grid["swma_length"],
)
