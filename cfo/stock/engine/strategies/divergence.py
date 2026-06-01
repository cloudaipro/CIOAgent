import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings


def detect_divergence(df, price, indicator, tol: int = 3):
    """Detects divergence between price swings and swings in an indicator.
    tol : int, optional
    The number of candles which conditions must be met within. The
    default is 3.
    """
    data = pd.DataFrame(index=df.index)

    data[["price_LL", "price_HL", "price_HH", "price_LH"]] = classify_swings(
        find_swings(df[price])
    )[["LL", "HL", "HH", "LH"]]
    data[["price_sLL", "price_sHL", "price_sHH", "price_sLH"]] = data[["price_LL", "price_HL", "price_HH", "price_LH"]].rolling(window=tol).sum()
    data["price_swing_cnt"] = data[
        ["price_sLL", "price_sHL", "price_sHH", "price_sLH"]
    ].sum(axis=1)

    data[["_LL", "_HL", "_HH", "_LH"]] = classify_swings(find_swings(df[indicator]))[
        ["LL", "HL", "HH", "LH"]
    ]
    data[["_sLL", "_sHL", "_sHH", "_sLH"]] = (
        data[["_LL", "_HL", "_HH", "_LH"]].rolling(window=tol).sum()
    )
    data["_swing_cnt"] = data[["_sLL", "_sHL", "_sHH", "_sLH"]].sum(
        axis=1
    )

    data[f"{indicator}_DIVERGENCE_BULL"] = 0
    data[f"{indicator}_DIVERGENCE_BEAR"] = 0

    data.loc[
        (data["price_LL"] == True) & (data["_swing_cnt"] == 1) & (data["_sHL"] == 1),
        f"{indicator}_DIVERGENCE_BULL",
    ] = 1
    data.loc[
        (data["_HL"] == True)
        & (data["price_swing_cnt"] == 1)
        & (data["price_sLL"] == 1),
        f"{indicator}_DIVERGENCE_BULL",
    ] = 1

    data.loc[
        (data["price_HH"] == True) & (data["_swing_cnt"] == 1) & (data["_sLH"] == 1),
        f"{indicator}_DIVERGENCE_BEAR",
    ] = 1
    data.loc[
        (data["_LH"] == True)
        & (data["price_swing_cnt"] == 1)
        & (data["price_sHH"] == 1),
        f"{indicator}_DIVERGENCE_BEAR",
    ] = 1

    divergence_signals = pd.DataFrame(index=df.index)
    divergence_signals[f"{indicator}_DIVERGENCE_BULL"] = data[
        f"{indicator}_DIVERGENCE_BULL"
    ]
    divergence_signals[f"{indicator}_DIVERGENCE_BEAR"] = data[
        f"{indicator}_DIVERGENCE_BEAR"
    ]

    del data
    gc.collect()
    return divergence_signals
