import pandas as pd
import pandas_ta
import numpy as np
import gc
from strategies.ta_util import detect_consolidation
from parameter_grid import parameter_grid

"""
reef: https://blog.xcaldata.com/enhance-your-approach-using-the-elder-force-index-efi-to-understand-market-trends/
"""
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
    **kwargs
):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    drift = kwargs.get("drift", 1)
    length = kwargs.get("length", 13)
    consolidation_len = kwargs.get("consolidation_len", 10)

    if consolidation_data is None:
        consolidation_data = detect_consolidation(
            df,
            high,
            low,
            close,
            volume,
            length=consolidation_len,
            std=std,
            scalar=scalar,
        )

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["efi"] = df.ta.efi(
        close=close, volume=volume, length=length, drift=drift
    )
    data["p_efi"] = data["efi"].shift(1)
    data["ZEROCROSS_BULL"] = 0
    data["ZEROCROSS_BEAR"] = 0
    data.loc[
        (data["efi"] > 0)
        & (data["p_efi"] < 0)
        & (consolidation_data["consolidation"] == 0.0),
        "ZEROCROSS_BULL",
    ] = 1
    data.loc[
        (data["efi"] < 0)
        & (data["p_efi"] > 0)
        & (consolidation_data["consolidation"] == 0.0),
        "ZEROCROSS_BEAR",
    ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_EFI_ZEROCROSS_BULL{suffix}"] = data["ZEROCROSS_BULL"]
    buy_sell_signals[f"{prefix}c_EFI_ZEROCROSS_BEAR{suffix}"] = data["ZEROCROSS_BEAR"]

    del data
    gc.collect()

    return buy_sell_signals

default_efi_signal = "c_EFI_ZEROCROSS_BULL"
efi_grid_of_parameter = parameter_grid({
    "length": [5, 9, 13, 20],
    "drift": [1, 2, 3, 4, 5],
    "consolidation_len": [3, 5, 7, 10, 15, 20],
}
)