import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from parameter_grid import parameter_grid


"""
https://app.luxalgo.com/library/indicator/Sequencer/
https://www.fairleadstrategies.com/three-classes-of-indicators
https://trendspider.com/learning-center/td-sequential-a-comprehensive-guide-for-traders/
https://tradingcenter.org/index.php/learn/technical-analysis/328-how-to-trade-td-sequential
https://demark.com/sequential-indicator/
"""


def _td_seq(close):
    """TD Sequential setup counts (replaces pandas_ta.td_seq, removed in pandas_ta 0.4).

    Returns a DataFrame with TD_SEQ_UP / TD_SEQ_DN: the running count of consecutive
    closes greater / less than the close 4 bars earlier (TD price-flip setup).
    """
    c = pd.Series(close).to_numpy(dtype="float64")
    up = np.zeros(len(c))
    dn = np.zeros(len(c))
    u = d = 0
    for i in range(len(c)):
        if i >= 4 and c[i] > c[i - 4]:
            u += 1
            d = 0
        elif i >= 4 and c[i] < c[i - 4]:
            d += 1
            u = 0
        else:
            u = d = 0
        up[i] = u
        dn[i] = d
    return pd.DataFrame(
        {"TD_SEQ_UP": up, "TD_SEQ_DN": dn}, index=getattr(close, "index", None)
    )


def create_signals(df, close, prefix="", suffix="", **kwargs):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    # Create a DataFrame to store calculated values
    data = _td_seq(df[close])
    data.index = df.index
    data.fillna(0, inplace=True)
    # data[close] = df[close]
    # data["8th_close"] = data[close].shift(1)
    # data["7th_close"] = data[close].shift(2)
    # data["6th_close"] = data[close].shift(3)

    data["DOWN_BULL"] = 0
    data["UP_BEAR"] = 0
    data.loc[data["TD_SEQ_DN"] == 9, "DOWN_BULL"] = 1
    data.loc[data["TD_SEQ_UP"] == 9, "UP_BEAR"] = 1

    # data["P_BUY"] = 0
    # data["P_SELL"] = 0
    # # The Setup is classified as “Perfected” when the 9 is completed and the 6 and 7 bars have been exceeded.
    # data.loc[
    #     (data["TD_SEQ_DN"] == 9)
    #     & (data[close] < data["7th_close"])
    #     & (data[close] < data["6th_close"]),
    #     "P_BUY",
    # ] = 1
    # data.loc[
    #     (data["TD_SEQ_UP"] == 9)
    #     & (data[close] > data["7th_close"])
    #     & (data[close] > data["6th_close"]),
    #     "P_SELL",
    # ] = 1

    # data.loc[
    #         (data["TD_SEQ_DN"] == 9)
    #         & ((data[close] < data["8th_close"]) | (data["8th_close"] < data["7th_close"])),
    #         "P_BUY",
    #     ] = 1
    # data.loc[
    #         (data["TD_SEQ_UP"] == 9)
    #         & ((data[close] > data["8th_close"]) | (data["8th_close"] > data["7th_close"])),
    #         "P_SELL",
    #     ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_TDSEQ_DOWN_BULL{suffix}"] = data["DOWN_BULL"]
    buy_sell_signals[f"{prefix}c_TDSEQ_UP_BEAR{suffix}"] = data["UP_BEAR"]
    # buy_sell_signals[f"{prefix}c_TD_SEQ_P_BUY{suffix}"] = data["P_BUY"]
    # buy_sell_signals[f"{prefix}c_TD_SEQ_P_SELL{suffix}"] = data["P_SELL"]

    del data
    gc.collect()

    return buy_sell_signals

default_td_seq_signal = "c_TDSEQ_DOWN_BULL"
tdseq_grid_of_parameter = parameter_grid(
    {
        "drift": [1],
    }
)
