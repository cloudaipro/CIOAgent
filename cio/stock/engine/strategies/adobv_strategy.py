import pandas as pd
import pandas_ta
import numpy as np
import gc
from parameter_grid import parameter_grid


"""
https://school.stockcharts.com/doku.php?id=technical_indicators:accumulation_distribution_line
"""
def create_signals(
    df,
    high,
    low,
    close,
    volume,
    prefix="",
    suffix="",
    **kwargs,
):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    fast = kwargs.get("fast", 20)
    slow = kwargs.get("slow", 65)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["sma20"] = df.ta.sma(close=close, length=fast)
    data["sma65"] = df.ta.sma(close=close, length=slow)
    data["ad"] = df.ta.ad(high=high, low=low, close=close, volume=volume)
    data["obv"] = df.ta.obv(close=close, volume=volume)
    data["ad20"] = data.ta.sma(close="ad", length=fast)
    data["ad65"] = data.ta.sma(close="ad", length=slow)
    data["obv20"] = data.ta.sma(close="obv", length=fast)
    data["obv65"] = data.ta.sma(close="obv", length=slow)

    data["DIVERGENCE_BULL"] = 0
    data["DIVERGENCE_BEAR"] = 0

    """
    [Daily Close < Daily SMA(65,Daily Close)] 
    AND [Daily AccDist > Daily AccDist Signal (65)] 
    AND [Daily OBV > Daily OBV Signal(65)] 
    AND [Daily Close < Daily SMA(20,Daily Close)] 
    AND [Daily AccDist > Daily AccDist Signal (20)] 
    AND [Daily OBV > Daily OBV Signal(20)]
    """
    data.loc[
        (data["close"] < data["sma65"])
        & (data["ad"] > data["ad65"])
        & (data["obv"] > data["obv65"])
        & (data["close"] < data["sma20"])
        & (data["ad"] > data["ad20"])
        & (data["obv"] > data["obv20"]),
        "DIVERGENCE_BULL",
    ] = 1

    """
    [Daily Close > Daily SMA(65,Daily Close)] 
    AND [Daily AccDist < Daily AccDist Signal (65)] 
    AND [Daily OBV < Daily OBV Signal(65)] 
    AND [Daily Close > Daily SMA(20,Daily Close)] 
    AND [Daily AccDist < Daily AccDist Signal (20)] 
    AND [Daily OBV < Daily OBV Signal(20)]
    """
    data.loc[
        (data["close"] > data["sma65"])
        & (data["ad"] < data["ad65"])
        & (data["obv"] < data["obv65"])
        & (data["close"] > data["sma20"])
        & (data["ad"] < data["ad20"])
        & (data["obv"] < data["obv20"]),
        "DIVERGENCE_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_ADOBV_DIVERGENCE_BULL{suffix}"] = data[
        "DIVERGENCE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_ADOBV_DIVERGENCE_BEAR{suffix}"] = data[
        "DIVERGENCE_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals


default_ad_obv_signal = "c_ADOBV_DIVERGENCE_BULL"
adobv_grid_of_parameter = parameter_grid(
    {
        "fast": [3, 5, 9, 20],
        "slow": [9, 15, 21, 65],
    },
    lambda grid: (grid["fast"] < grid["slow"]),
    # lambda grid: (grid["fast"] == 3 and grid["slow"] == 9)
    # or (grid["fast"] == 5 and grid["slow"] == 15)
    # or (grid["fast"] == 9 and grid["slow"] == 21)
    # or (grid["fast"] == 20 and grid["slow"] == 65),
)
