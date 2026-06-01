import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import detect_divergence
from parameter_grid import parameter_grid
from strategies.ta_util import crossover_signal

"""
https://www.tradingview.com/support/solutions/43000502329-know-sure-thing-kst/
parameters example:
Daily (10, 15, 20, 30, 10, 10, 10, 15, 9)
Weekly (10, 13, 15, 20, 10, 13, 15, 20, 9)
Monthly (9, 12, 18, 24, 6, 6, 6, 9, 9)
"""
def create_signals(
    df,
    close,
    roc1=None,
    roc2=None,
    roc3=None,
    roc4=None,
    sma1=None,
    sma2=None,
    sma3=None,
    sma4=None,
    prefix="",
    suffix="",
    **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    # Validate arguments
    roc1 = int(roc1) if roc1 and roc1 > 0 else 10
    roc2 = int(roc2) if roc2 and roc2 > 0 else 15
    roc3 = int(roc3) if roc3 and roc3 > 0 else 20
    roc4 = int(roc4) if roc4 and roc4 > 0 else 30

    sma1 = int(sma1) if sma1 and sma1 > 0 else 10
    sma2 = int(sma2) if sma2 and sma2 > 0 else 10
    sma3 = int(sma3) if sma3 and sma3 > 0 else 10
    sma4 = int(sma4) if sma4 and sma4 > 0 else 15

    signal = kwargs.get("signal", 9)  # int(signal) if signal and signal > 0 else 9
    ema_length = kwargs.get(
        "ema_length", 9
    )  # int(ema_length) if ema_length and ema_length > 0 else 9

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data[["kst", "signal"]] = df.ta.kst(
        close=close,
        roc1=roc1,
        roc2=roc2,
        roc3=roc3,
        roc4=roc4,
        sma1=sma1,
        sma2=sma2,
        sma3=sma3,
        sma4=sma4,
        signal=signal
    )
    data["p_kst"] = data["kst"].shift(1)
    data["p_signal"] = data["signal"].shift(1)

    data[
        [
            "CROSSOVER_BULL",
            "CROSSOVER_BEAR",
            "CROSSOVER_CSLS",
        ]
    ] = crossover_signal(data, "kst", "signal", zero_line_crossover=False)

    ## divergence
    data["ema"] = df.ta.ema(close=close, length=ema_length)
    data[["DIVERGENCE_BULL", "DIVERGENCE_BEAR"]] = detect_divergence(
        data["ema"], data["signal"]
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_KST_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_KST_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}c_KST_CROSSOVER_CSLS{suffix}"] = data["CROSSOVER_CSLS"]
    buy_sell_signals[f"{prefix}c_KST_DIVERGENCE_BULL{suffix}"] = data[
        "DIVERGENCE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_KST_DIVERGENCE_BEAR{suffix}"] = data[
        "DIVERGENCE_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_kst_signal = "c_KST_BULL"
kst_grid_of_parameter = parameter_grid(
    {
        "signal":[5, 9, 14]
    },
)
