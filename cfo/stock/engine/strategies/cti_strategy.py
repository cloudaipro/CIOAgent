import pandas as pd
import pandas_ta
import numpy as np
import gc
from parameter_grid import parameter_grid

"""
ref: https://tlc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/C-D/CorrelationTrendIndicator#:~:text=Correlation%20Trend%20Indicator%20is%20a,a%20positive%2Dslope%20straight%20line.
https://financial-hacker.com/petra-on-programming-a-unique-trend-indicator/
"""


def create_signals(df, close, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    fast = kwargs.get("fast", 5) 
    slow = kwargs.get("slow", 10) 

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["fast"] = df.ta.cti(close=close, length=fast)
    data["slow"] = df.ta.cti(close=close, length=slow)
    data["p_fast"] = data["fast"].shift(1)
    data["p_slow"] = data["slow"].shift(1)

    data["CROSSOVER_BULL"] = 0
    data["CROSSOVER_BEAR"] = 0

    data.loc[
        (data["fast"] < -0.5)
        & (data["slow"] < -0.5)
        & (data["fast"] > data["slow"])
        & (data["p_fast"] < data["p_slow"]),
        "CROSSOVER_BULL",
    ] = 1
    data.loc[
        (data["fast"] > 0.5)
        & (data["slow"] > 0.5)
        & (data["fast"] < data["slow"])
        & (data["p_fast"] > data["p_slow"]),
        "CROSSOVER_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_CTI_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_CTI_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_cti_signal = "c_CTI_CROSSOVER_BULL"
cti_grid_of_parameter = parameter_grid(
    {
        "fast": [3, 5, 7],
        "slow": [6, 10, 14],
    },
    lambda grid: grid["slow"] > grid["fast"],
)
