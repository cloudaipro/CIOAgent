import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import candles_between_signed
from parameter_grid import parameter_grid


"""
https://tlc.thinkorswim.com/center/reference/Tech-Indicators/studies-library/T-U/TTM-Squeeze
"""
def create_signals(
    df,
    high,
    low,
    close,
    bb_length=None,
    bb_std=None,
    kc_length=None,
    kc_scalar_wide=None,
    kc_scalar_normal=None,
    kc_scalar_narrow=None,
    mom_length=None,
    mom_smooth=None,
    prefix="",
    suffix="",
    **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    bb_length = int(bb_length) if bb_length and bb_length > 0 else 20
    bb_std = float(bb_std) if bb_std and bb_std > 0 else 2.0
    kc_length = int(kc_length) if kc_length and kc_length > 0 else 20
    kc_scalar_wide = float(kc_scalar_wide) if kc_scalar_wide and kc_scalar_wide > 0 else 2
    kc_scalar_normal = float(kc_scalar_normal) if kc_scalar_normal and kc_scalar_normal > 0 else 1.5
    kc_scalar_narrow = float(kc_scalar_narrow) if kc_scalar_narrow and kc_scalar_narrow > 0 else 1
    mom_length = int(mom_length) if mom_length and mom_length > 0 else 12
    mom_smooth = int(mom_smooth) if mom_smooth and mom_smooth > 0 else 6

    # Create a DataFrame to store calculated values
    # data = pd.DataFrame(index=df.index)
    data = df.ta.squeeze_pro(
        high=high,
        low=low,
        close=close,
        bb_length=bb_length,
        bb_std=bb_std,
        kc_length=kc_length,
        kc_scalar_wide=kc_scalar_wide,
        kc_scalar_normal=kc_scalar_normal,
        kc_scalar_narrow=kc_scalar_narrow,
        mom_length=mom_length,
        mom_smooth=mom_smooth,
    )
    data["ON_CSLS"] = candles_between_signed(data["SQZPRO_ON_WIDE"])
    data["OFF_CSLS"] = candles_between_signed(data["SQZPRO_OFF"])
    data["up_hist_mom_pos"] = 0
    data["up_hist_mom_neg"] = 0
    data["down_hist_mom_pos"] = 0
    data["down_hist_mom_neg"] = 0
    squeeze_data = data.iloc[:,0]
    data["sqz_mom_sign"] = np.sign(squeeze_data.diff())
    data.loc[(squeeze_data > 0) & (data["sqz_mom_sign"] == 1), "up_hist_mom_pos"] = 1
    data.loc[(squeeze_data > 0) & (data["sqz_mom_sign"] == -1), "up_hist_mom_neg"] = 1
    data.loc[(squeeze_data < 0) & (data["sqz_mom_sign"] == 1), "down_hist_mom_pos"] = 1
    data.loc[(squeeze_data < 0) & (data["sqz_mom_sign"] == -1), "down_hist_mom_neg"] = 1
    data["up_hist_mom_pos_CSLS"] = candles_between_signed(data["up_hist_mom_pos"])
    data["up_hist_mom_neg_CSLS"] = candles_between_signed(data["up_hist_mom_neg"])
    data["down_hist_mom_pos_CSLS"] = candles_between_signed(data["down_hist_mom_pos"])
    data["down_hist_mom_neg_CSLS"] = candles_between_signed(data["down_hist_mom_neg"])
    data[["bandwidth", "percent"]] = df.ta.bbands(close=close, length=bb_length, std=bb_std).iloc[:, 3:5]

    p_squeeze_data = squeeze_data.shift(1)
    data["ZEROCROSS_BULL"] = 0
    data["ZEROCROSS_BEAR"] = 0
    data.loc[(squeeze_data > 0) & (p_squeeze_data < 0), "ZEROCROSS_BULL"] = 1
    data.loc[(squeeze_data < 0) & (p_squeeze_data > 0), "ZEROCROSS_BEAR"] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_BANDWIDTH{suffix}"] = data["bandwidth"]
    buy_sell_signals[f"{prefix}f_BBAND_PERCENT{suffix}"] = data["percent"]

    buy_sell_signals[f"{prefix}c_SQZ_OFF{suffix}"] = data.iloc[:, 4]
    buy_sell_signals[f"{prefix}f_SQZ_OFF_CSLS{suffix}"] = data["OFF_CSLS"]

    buy_sell_signals[f"{prefix}c_SQZ_ON_WIDE{suffix}"] = data.iloc[:, 1]
    buy_sell_signals[f"{prefix}c_SQZ_ON_NORMAL{suffix}"] = data.iloc[:, 2]
    buy_sell_signals[f"{prefix}c_SQZ_ON_NARROW{suffix}"] = data.iloc[:, 3]
    buy_sell_signals[f"{prefix}f_SQZ_ON_CSLS{suffix}"] = data["ON_CSLS"]

    buy_sell_signals[f"{prefix}c_SQZ_ZEROCROSS_BULL{suffix}"] = data["ZEROCROSS_BULL"]
    buy_sell_signals[f"{prefix}c_SQZ_ZEROCROSS_BEAR{suffix}"] = data["ZEROCROSS_BEAR"]

    buy_sell_signals[f"{prefix}c_SQZ_HISTMOMPOS_UP{suffix}"] = data["up_hist_mom_pos"]
    buy_sell_signals[f"{prefix}f_SQZ_HISTMOMPOS_UP_CSLS{suffix}"] = data[
        "up_hist_mom_pos_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_SQZ_HISTMOMNEG_UP{suffix}"] = data["up_hist_mom_neg"]
    buy_sell_signals[f"{prefix}f_SQZ_HISTMOMNEG_UP_CSLS{suffix}"] = data[
        "up_hist_mom_neg_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_SQZ_HISTMOMPOS_DOWN{suffix}"] = data[
        "down_hist_mom_pos"
    ]
    buy_sell_signals[f"{prefix}f_SQZ_HISTMOMPOS_DOWN_CSLS{suffix}"] = data[
        "down_hist_mom_pos_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_SQZ_HISTMOMNEG_DOWN{suffix}"] = data[
        "down_hist_mom_neg"
    ]
    buy_sell_signals[f"{prefix}f_SQZ_HISTMOMNEG_DOWN_CSLS{suffix}"] = data[
        "down_hist_mom_neg_CSLS"
    ]

    del data
    gc.collect()

    return buy_sell_signals

squeeze_grid_of_parameter = parameter_grid(
    {
        "drift": [1],
    }
)
