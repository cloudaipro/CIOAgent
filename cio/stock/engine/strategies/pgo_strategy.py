import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import detect_divergence
from parameter_grid import parameter_grid


"""
https://www.marketinout.com/stock-screener/industry.php?picker=pgo#:~:text=Pretty%20Good%20Oscillator%20(PGO)%20Stock%20Screener&text=Developed%20by%20Mark%20Johnson%2C%20the,system%20for%20longer%2Dterm%20trades

"""
def create_signals(
    df, high, low, close, prefix="", suffix="", **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 13)
    ema_length = kwargs.get("ema_length", 9) # int(ema_length) if ema_length and ema_length > 0 else 9
    limit = kwargs.get("limit", 3)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["pgo"] = df.ta.pgo(high=high, low=low, close=close, length=length)
    data["p_pgo"] = data["pgo"].shift(1)

    data["CROSSOVER_BULL"] = 0
    data["CROSSOVER_BEAR"] = 0
    data.loc[
        (data["pgo"] > limit) & (data["p_pgo"] < limit),
        "CROSSOVER_BULL",
    ] = 1
    data.loc[
        (data["pgo"] < -limit) & (data["p_pgo"] > -limit),
        "CROSSOVER_BEAR",
    ] = 1

    ## divergence
    data["ema"] = df.ta.ema(close=close, length=ema_length)
    data["pgo_signal"] = data.ta.ema(close="pgo", length=ema_length)
    data[["DIVERGENCE_BULL", "DIVERGENCE_BEAR"]] = detect_divergence(
        data["ema"], data["pgo_signal"]
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_PGO_CROSSOVER_BULL{suffix}"] = data["CROSSOVER_BULL"]
    buy_sell_signals[f"{prefix}c_PGO_CROSSOVER_BEAR{suffix}"] = data["CROSSOVER_BEAR"]
    buy_sell_signals[f"{prefix}c_PGO_DIVERGENCE_BULL{suffix}"] = data[
        "DIVERGENCE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_PGO_DIVERGENCE_BEAR{suffix}"] = data[
        "DIVERGENCE_BEAR"
    ]

    del data
    gc.collect()

    return buy_sell_signals

default_pgo_signal = "c_PGO_CROSSOVER_BULL"
pgo_grid_of_parameter = parameter_grid({
        "length": [5, 9, 13, 20],
        "limit": [1.5, 2, 2.5, 3],
    }
)
