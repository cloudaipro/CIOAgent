import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid


"""
https://medium.com/@mkrt.crypto.arsenal/該買-該賣-來問技術指標-14-bop-能量均衡指標指標-c3ca5192c552
"""
def create_signals(df, open, high, low, close, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    sma_length = kwargs.get("sma_length", 10)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["sma"] = df.ta.sma(close=close, length=sma_length)
    data["bop"] = df.ta.bop(open=open, high=high, low=low, close=close)

    data["TREND_UP"] = 0
    data["TREND_DOWN"] = 0
    # 收盤價 > SMA 且 BOP > 0。表示買方力道強，設定為1
    data.loc[(data["close"] > data["sma"]) & (data["bop"] > 0), "TREND_UP"] = 1
    # 收盤價 < SMA 且 BOP < 0。表示賣方力道強，設定為1
    data.loc[(data["close"] < data["sma"]) & (data["bop"] < 0), "TREND_DOWN"] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_BOP_TREND_UP{suffix}"] = data["TREND_UP"]
    buy_sell_signals[f"{prefix}c_BOP_TREND_DOWN{suffix}"] = data["TREND_DOWN"]

    del data
    gc.collect()

    return buy_sell_signals

default_bop_signal = "c_BOP_TREND_UP"
bop_grid_of_parameter = parameter_grid(
    {
        "sma_length": [3, 5, 7, 10, 15, 20],
    }
)
