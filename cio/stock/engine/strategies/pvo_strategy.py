import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import (
    find_swings,
    classify_swings,
)
from strategies.ta_util import (
    detect_divergence,
    candles_between_crosses,
    candles_between_signed,
    crossover_signal,
)
from parameter_grid import parameter_grid


"""
Many professional traders find the default settings (12, 26 and 9) to be too slow, causing late entry and exit to and from a trade. 
You may want to customize/format the settings and see how well they perform on a demo account. Some alternative settings to try are:
ref: https://school.stockcharts.com/doku.php?id=technical_indicators:percentage_volume_oscillator_pvo#:~:text=The%20Percentage%20Volume%20Oscillator%20(PVO,a%20histogram%20and%20a%20centerline.
8, 21, 5
3, 17, 5
3, 10, 16
"""
def create_signals(df, close, volume, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    fast = kwargs.get("fast", 12)  # int(fast) if fast and fast > 0 else 12
    slow = kwargs.get("slow", 26)  # int(slow) if slow and slow > 0 else 26
    signal = kwargs.get("signal", 9)  # int(signal) if signal and signal > 0 else 9
    cls_limit = kwargs.get("cls_limit", 4)
    # Create a DataFrame to store calculated values

    data = df.ta.pvo(volume=volume, fast=fast, slow=slow, signal=signal)
    pvo_line = data.iloc[:, 0]
    histogram_line = data.iloc[:, 1]
    signal_line = data.iloc[:, 2]
    data["p_pvo_line"] =pvo_line.shift(1)
    data["p_signal_line"] = signal_line.shift(1)
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

    # PPO Cross with PVO Positive
    data["pvo_sign_CSLS"] = candles_between_signed(pvo_line)

    ppo_data = df.ta.ppo(close=close, fast=fast, slow=slow, signal=signal)

    data["ppo_histogram_sign_CSLS"] = candles_between_signed(ppo_data.iloc[:, 1])

    data["CROSSOVERPVOPOSITIVE_BULL"] = 0
    """
    PPO Bullish Cross with PVO Positive
    1. PPO(12,26,9) moved above the PPO Signal Line (==> HISTOGRAM(12,26,9) moved into positive territory)
    AND
    2. the PVO(12,26,9) moved into positive territory to show increasing volume
    
    pseudo code:
    [Daily PPO Line(12,26,9,Daily Close) crosses Daily PPO Signal(12,26,9,Daily Close)] 
    AND [Daily PVO Line(12,26,9) crosses 0]
    """
    data.loc[
        (data["ppo_histogram_sign_CSLS"] == 1)
        & (data["pvo_sign_CSLS"] > 1)
        & (data["pvo_sign_CSLS"] < cls_limit),
        "CROSSOVERPVOPOSITIVE_BULL",
    ] = 1
    data.loc[
        (data["pvo_sign_CSLS"] == 1)
        & (data["ppo_histogram_sign_CSLS"] > 1)
        & (data["ppo_histogram_sign_CSLS"] < cls_limit),
        "CROSSOVERPVOPOSITIVE_BULL",
    ] = 1

    data["CROSSOVERPVOPOSITIVE_BEAR"] = 0
    """
    PPO Bearish Cross with PVO Positive
    1. PPO(12,26,9) moved below the PPO Signal Line (==> HISTOGRAM(12,26,9) moved into negitive territory)
    AND
    2. the PVO(12,26,9) moved into positive territory to show increasing volume
    
    pseudo code:
    [Daily PPO Signal(12,26,9,Daily Close) crosses Daily PPO Line(12,26,9,Daily Close)] 
AND [Daily PVO Line(12,26,9) crosses 0]
    """
    data.loc[
        (data["ppo_histogram_sign_CSLS"] == -1)
        & (data["pvo_sign_CSLS"] > 1)
        & (data["pvo_sign_CSLS"] < cls_limit),
        "CROSSOVERPVOPOSITIVE_BEAR",
    ] = 1
    data.loc[
        (data["pvo_sign_CSLS"] == 1)
        & (data["ppo_histogram_sign_CSLS"] < -1)
        & (data["ppo_histogram_sign_CSLS"] > -cls_limit),
        "CROSSOVERPVOPOSITIVE_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_PVO_HISTOGRAM_CSLS{suffix}"] = (
        histogram_swings["CSLS"] * histogram_swings["Trend"]
    )
    buy_sell_signals[f"{prefix}c_PVO_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_PVO_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}f_PVO_CROSSOVER_CSLS{suffix}"] = data["CROSSOVER_CSLS"]
    buy_sell_signals[f"{prefix}c_PVO_ZEROCROSS_BULL{suffix}"] = data["ZEROCROSS_BULL"]
    buy_sell_signals[f"{prefix}c_PVO_ZEROCROSS_BEAR{suffix}"] = data["ZEROCROSS_BEAR"]

    buy_sell_signals[f"{prefix}c_PPO_CROSSOVERPVOPOSITIVE_BULL{suffix}"] = data[
        "CROSSOVERPVOPOSITIVE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_PPO_CROSSOVERPVOPOSITIVE_BEAR{suffix}"] = data[
        "CROSSOVERPVOPOSITIVE_BEAR"
    ]

    del data
    del ppo_data
    gc.collect()

    return buy_sell_signals

default_pvo_signal = "c_PVO_CROSSOVER_BULL"
pvo_grid_of_parameter = parameter_grid(
    {
        "fast": [3, 8, 12],
        "slow": [17, 21, 26],
        "signal": [5, 9],
        "cls_limit": [3, 4, 5],
    },
    lambda grid: (grid["fast"] == 12 and grid["slow"] == 26 and grid["signal"] == 9)
    or (grid["fast"] == 8 and grid["slow"] == 21 and grid["signal"] == 5)
    or (grid["fast"] == 3 and grid["slow"] == 17 and grid["signal"] == 5),
)
