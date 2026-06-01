import pandas as pd
import pandas_ta
import numpy as np
import gc
from parameter_grid import parameter_grid

"""
https://www.whselfinvest.com/en-be/trading-platform/free-trading-signals/15-dr-alexander-elder-ray-bull-power-bear-power
"""
def create_signals(df, high, low, close, prefix="", suffix="", **kwargs):
    """
    Create buy and sell signals based on the ERI strategy.

    Args:
        df (pandas.DataFrame): The input DataFrame containing the stock market data.
        high (str): The column name for the high prices.
        low (str): The column name for the low prices.
        close (str): The column name for the closing prices.
        length (int): The length parameter for the ERI indicator.
        prefix (str, optional): The prefix to be added to the signal column names. Defaults to "".
        suffix (str, optional): The suffix to be added to the signal column names. Defaults to "".

    Returns:
        pandas.DataFrame: A DataFrame containing the buy and sell signals.

    """
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    length = kwargs.get("length", 13)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["p_close"] = data["close"].shift(1)
    data[["bull", "bear"]] = df.ta.eri(high=high, low=low, close=close, length=length)
    data["ema"] = df.ta.ema(close=close, length=length)
    data["p_ema"] = data["ema"].shift(1)
    data["p_bull"] = data["bull"].shift(1)
    data["p_bear"] = data["bear"].shift(1)

    data["DIVERGENCE_BULL"] = 0
    data["DIVERGENCE_BEAR"] = 0

    """
    The market price (EMA) is going up.
    The Bear Power is negative (below zero), but going up.
    The current Bull Power bar is higher than the previous bar.
    The Bear Power indicator shows a bullish divergence with the market price. This means the indicator goes up and the price down.
    """
    data.loc[
        (data["ema"] > data["p_ema"])
        & (data["bear"] < 0)
        & (data["bear"] > data["p_bear"])
        & (data["bull"] > data["p_bull"])
        & (data["close"] < data["p_close"]),
        "DIVERGENCE_BEAR",
    ] = 1
    """
    The market price (EMA) is going up.
    The Bull Power is positive (above zero), but going down.
    The current Bear Power bar is lower than the previous bar.
    The Bull Power shows a bearish divergence with the market price. This means the indicator goes down and the market price goes up
    """
    data.loc[
        (data["ema"] > data["p_ema"])
        & (data["bull"] > 0)
        & (data["bull"] < data["p_bull"])
        & (data["bear"] < data["p_bear"])
        & (data["close"] > data["p_close"]),
        "DIVERGENCE_BULL",
    ] = 1
    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_ERI_DIVERGENCE_BULL{suffix}"] = data["DIVERGENCE_BULL"]
    buy_sell_signals[f"{prefix}c_ERI_DIVERGENCE_BEAR{suffix}"] = data["DIVERGENCE_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_eri_signal = "c_ERI_DIVERGENCE_BULL"
eri_grid_of_parameter = parameter_grid({
    "length": [5, 9, 13, 20],
})
