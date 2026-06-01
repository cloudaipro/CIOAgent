import pandas as pd
import pandas_ta
import numpy as np
import gc
from strategies.ta_util import detect_consolidation
from parameter_grid import parameter_grid


def create_signals(
    df,
    high,
    low,
    close,
    volume,
    consolidation_data=None,
    std=2.0,
    scalar=1.2,
    prefix="",
    suffix="",
    **kwargs,
):

    fast = kwargs.get("fast", 3)
    slow = kwargs.get("slow", 10)

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    if consolidation_data is None:
        consolidation_data = detect_consolidation(
            df,
            high,
            low,
            close,
            volume,
            length=slow,
            std=std,
            scalar=scalar,
        )

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["adosc"] = df.ta.adosc(high=high, low=low, close=close, volume=volume, fast=fast, slow=slow)
    data["p_adosc"] = data["adosc"].shift(1)
    data["ZEROCROSS_BULL"] = 0
    data["ZEROCROSS_BEAR"] = 0
    data.loc[
        (data["adosc"] > 0)
        & (data["p_adosc"] < 0)
        & (consolidation_data["consolidation"] == 0.0),
        "ZEROCROSS_BULL",
    ] = 1
    data.loc[
        (data["adosc"] < 0)
        & (data["p_adosc"] > 0)
        & (consolidation_data["consolidation"] == 0.0),
        "ZEROCROSS_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_ADOSC_ZEROCROSS_BULL{suffix}"] = data[
        "ZEROCROSS_BULL"
    ]
    buy_sell_signals[f"{prefix}c_ADOSC_ZEROCROSS_BEAR{suffix}"] = data[
        "ZEROCROSS_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_adosc_signal = "c_ADOSC_ZEROCROSS_BULL"
adosc_grid_of_parameter = parameter_grid(
    {
        "fast": [3],
        "slow": [10, 7, 5],
    },
)
