import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import (
    find_swings,
    classify_swings,
)
from strategies.ta_util import detect_divergence, crossover_signal
from parameter_grid import parameter_grid


"""
Many professional traders find the default settings (12, 26 and 9) to be too slow, causing late entry and exit to and from a trade. 
You may want to customize/format the settings and see how well they perform on a demo account. Some alternative settings to try are:
ref: https://trendspider.com/learning-center/the-percentage-price-oscillator-ppo-an-overview/#
"""
def create_signals(df, close, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    fast = kwargs.get("fast", 12)  # int(fast) if fast and fast > 0 else 12
    slow = kwargs.get("slow", 26)  # int(slow) if slow and slow > 0 else 26
    signal = kwargs.get("signal", 9)  # int(signal) if signal and signal > 0 else 9

    # Create a DataFrame to store calculated values
    data = df.ta.ppo(close=close, fast=fast, slow=slow, signal=signal)

    ppo_line = data.iloc[:, 0]
    histogram_line = data.iloc[:, 1]
    # signal_line = data.iloc[:, 2]
    histogram_swings = classify_swings(find_swings(histogram_line)).dropna()

    data[
        [
            "CROSSOVER_BULL",
            "CROSSOVER_BEAR",
            "CROSSOVER_CSLS",
            "ZEROCROSS_BULL",
            "ZEROCROSS_BEAR",
        ]
    ] = crossover_signal(
        data, data.columns.values[0], data.columns.values[2], zero_line_crossover=True
    )

    ## divergence
    data["ema"] = df.ta.ema(close=close, length=signal)
    data[["DIVERGENCE_BULL", "DIVERGENCE_BEAR"]] = detect_divergence(
        data["ema"], ppo_line
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_PPO_HISTOGRAM_CSLS{suffix}"] = (
        histogram_swings["CSLS"] * histogram_swings["Trend"]
    )
    buy_sell_signals[f"{prefix}c_PPO_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_PPO_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}f_PPO_CROSSOVER_CSLS{suffix}"] = data["CROSSOVER_CSLS"]
    buy_sell_signals[f"{prefix}c_PPO_ZEROCROSS_BULL{suffix}"] = data["ZEROCROSS_BULL"]
    buy_sell_signals[f"{prefix}c_PPO_ZEROCROSS_BEAR{suffix}"] = data["ZEROCROSS_BEAR"]

    buy_sell_signals[f"{prefix}c_PPO_DIVERGENCE_BULL{suffix}"] = data["DIVERGENCE_BULL"]
    buy_sell_signals[f"{prefix}c_PPO_DIVERGENCE_BEAR{suffix}"] = data["DIVERGENCE_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals


default_ppo_signal = "c_PPO_CROSSOVER_BULL"
ppo_grid_of_parameter = parameter_grid(
    {
        "fast": [3, 8, 12],
        "slow": [17, 21, 26],
        "signal": [5, 9],
    },
    lambda grid: (grid["fast"] == 12 and grid["slow"] == 26 and grid["signal"] == 9)
    or (grid["fast"] == 8 and grid["slow"] == 21 and grid["signal"] == 5)
    or (grid["fast"] == 3 and grid["slow"] == 17 and grid["signal"] == 5),
)
