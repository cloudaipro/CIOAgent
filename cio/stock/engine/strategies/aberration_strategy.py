import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import candles_between_signed
from parameter_grid import parameter_grid


"""
https://blog.xcaldata.com/exploring-aberration-unraveling-volatility-indicators/#:~:text=The%20Aberration%20is%20a%20volatility,valuable%20tools%20in%20decision%2Dmaking.
"""
def create_signals(df, high, low, close, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    length = kwargs.get("length", 5)
    atr_length = kwargs.get("atr_length", 15)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data[["zg", "sg", "xg", "atr"]] = df.ta.aberration(
        high=high, low=low, close=close, length=length, atr_length=atr_length
    )

    data["TREND"] = 0
    # 收盤價 > sg。表示買方力道強，設定為1
    data.loc[
        (data["close"] > data["sg"]), "TREND"
    ] = 1
    # 收盤價 < xg。表示賣方力道強，設定為1
    data.loc[
        (data["close"] < data["xg"]), "TREND"
    ] = -1

    data["TREND_CSLS"] = candles_between_signed(data["TREND"])

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_ABERRATION_TREND{suffix}"] = data["TREND"]
    buy_sell_signals[f"{prefix}f_ABERRATION_TREND_CSLS{suffix}"] = data["TREND_CSLS"]

    del data
    gc.collect()

    return buy_sell_signals

aberration_grid_of_parameter = parameter_grid(
    {
        "length": [3, 5, 7],
        "atr_length": [3, 5, 7, 10, 15, 20],
    }
)
