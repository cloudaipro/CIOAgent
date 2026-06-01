import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_data_swings, classify_swings
from parameter_grid import parameter_grid

"""
https://www.tradingview.com/support/solutions/43000589141-fisher-transform/
"""


def create_signals(df, high, low, length=None, signal=None, prefix="", suffix=""):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = int(length) if length and length > 0 else 9
    signal = int(signal) if signal and signal > 0 else 1

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data[["fisher", "signal"]] = df.ta.fisher(
        high=high, low=low, length=length, signal=signal
    )
    fisher_swings = classify_swings(find_data_swings(data["fisher"]))

    signals = pd.DataFrame(index=df.index)
    signals[f"{prefix}f_FISHER_CSLS{suffix}"] = (
        fisher_swings["CSLS"] * fisher_swings["Trend"]
    )
    signals.loc[fisher_swings["Last"].isna(), f"{prefix}f_FISHER_CSLS{suffix}"] = np.nan

    del data
    gc.collect()

    return signals
