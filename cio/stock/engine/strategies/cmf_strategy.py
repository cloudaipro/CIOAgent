import pandas as pd
import pandas_ta
import gc
from parameter_grid import parameter_grid

"""
https://zhuanlan.zhihu.com/p/38045262
"""
def create_signals(df, high, low, close, volume, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    length = kwargs.get("length", 20)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["cmf"] = df.ta.cmf(high=high, low=low, close=close, volume=volume, length=length)
    data["p_cmf"] = data["cmf"].shift(1)
    data["CMF_ZEROCROSS_BULL"] = 0
    data["CMF_ZEROCROSS_BEAR"] = 0

    data.loc[(data["cmf"] > 0.05) & (data["p_cmf"] <= 0.05), "CMF_ZEROCROSS_BULL"] = 1
    data.loc[(data["cmf"] < -0.05) & (data["p_cmf"] >= -0.05), "CMF_ZEROCROSS_BEAR"] = 1
    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_CMF_ZEROCROSS_BULL{suffix}"] = data[
        "CMF_ZEROCROSS_BULL"
    ]
    buy_sell_signals[f"{prefix}c_CMF_ZEROCROSS_BEAR{suffix}"] = data[
        "CMF_ZEROCROSS_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_cmf_signal = "c_CMF_ZEROCROSS_BULL"
cmf_grid_of_parameter = parameter_grid({
    "length": [3, 5, 10, 15, 20, 30, 50],
})
