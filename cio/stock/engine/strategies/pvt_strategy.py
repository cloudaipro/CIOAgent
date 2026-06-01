import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from sklearn.metrics import precision_recall_fscore_support
from parameter_grid import parameter_grid

"""
https://profitmart.in/knowledge-center/candlestick-patterns/what-is-a-volume-price-trend-indicator/#:~:text=Volume%2Dprice%20trend%20(VPT),given%20interval%20(usually%20daily).
"""


def create_signals(df, close, volume, prefix="", suffix="", **kwargs):

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix
    drift = kwargs.get("drift", 1)
    sma_length = kwargs.get("sma_length", 9)

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["pvt"] = df.ta.pvt(close=close, volume=volume, drift=drift)
    data["pvt_sma"] = data["pvt"].rolling(window=sma_length).mean()
    data["hist"] = data["pvt"] - data["pvt_sma"]
    data = pd.concat([data, classify_swings(find_swings(np.sign(data["hist"]), n=1))], axis=1)
    data["SUPPORT_BULL"] = 0
    data["SUPPORT_BEAR"] = 0

    data.loc[
        (data["Support"] == True) & (data["CSLS"] == 3),
        "SUPPORT_BULL",
    ] = 1
    data.loc[
        (data["Resistance"] == True) & (data["CSLS"] == 3),
        "SUPPORT_BEAR",
    ] = 1

    data[["pvt_LL", "pvt_HL", "pvt_HH", "pvt_LH"]] = classify_swings(
        find_swings(data["pvt"])
    )[["LL", "HL", "HH", "LH"]]
    data[["pvt_sLL", "pvt_sHL", "pvt_sHH", "pvt_sLH"]] = (
        data[["pvt_LL", "pvt_HL", "pvt_HH", "pvt_LH"]].rolling(window=3).sum()
    )
    data["pvt_swing_cnt"] = data[["pvt_sLL", "pvt_sHL", "pvt_sHH", "pvt_sLH"]].sum(axis=1)

    data[["price_LL", "price_HL", "price_HH", "price_LH"]] = classify_swings(
        find_swings(data["close"])
    )[["LL", "HL", "HH", "LH"]]
    data[["price_sLL", "price_sHL", "price_sHH", "price_sLH"]] = (
        data[["price_LL", "price_HL", "price_HH", "price_LH"]].rolling(window=3).sum()
    )
    data["price_swing_cnt"] = data[
        ["price_sLL", "price_sHL", "price_sHH", "price_sLH"]
    ].sum(axis=1)

    # data["PVT_DIVERGENCE_BULL"] = 0
    # data["PVT_DIVERGENCE_BEAR"] = 0

    # data.loc[
    #     (data["price_LL"] == True)
    #     & (data["pvt_swing_cnt"] == 1)
    #     & (data["pvt_sHL"] == 1),
    #     "PVT_DIVERGENCE_BULL",
    # ] = 1
    # data.loc[
    #     (data["pvt_HL"] == True)
    #     & (data["price_swing_cnt"] == 1)
    #     & (data["price_sLL"] == 1),
    #     "PVT_DIVERGENCE_BULL",
    # ] = 1
    # # # hiddern bullish divergence
    # # data.loc[
    # #     (data["price_HL"] == True)
    # #     & (data["pvt_swing_cnt"] == 1)
    # #     & (data["pvt_sLL"] == 1),
    # #     "PVT_DIVERGENCE_BULL",
    # # ] = 1
    # # data.loc[
    # #     (data["pvt_LL"] == True)
    # #     & (data["price_swing_cnt"] == 1)
    # #     & (data["price_sHL"] == 1),
    # #     "PVT_DIVERGENCE_BULL",
    # # ] = 1

    # data.loc[
    #     (data["price_HH"] == True)
    #     & (data["pvt_swing_cnt"] == 1)
    #     & (data["pvt_sLH"] == 1),
    #     "PVT_DIVERGENCE_BEAR",
    # ] = 1
    # data.loc[
    #     (data["pvt_LH"] == True)
    #     & (data["price_swing_cnt"] == 1)
    #     & (data["price_sHH"] == 1),
    #     "PVT_DIVERGENCE_BEAR",
    # ] = 1
    # # # hiddern bear divergence
    # # data.loc[
    # #     (data["price_LH"] == True)
    # #     & (data["pvt_swing_cnt"] == 1)
    # #     & (data["pvt_sHH"] == 1),
    # #     "PVT_DIVERGENCE_BEAR",
    # # ] = 1
    # # data.loc[
    # #     (data["pvt_HH"] == True)
    # #     & (data["price_swing_cnt"] == 1)
    # #     & (data["price_sLH"] == 1),
    # #     "PVT_DIVERGENCE_BEAR",
    # # ] = 1

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}f_PVT_TREND_CSLS{suffix}"] = data["CSLS"] * data["Trend"]
    # buy_sell_signals[f"{prefix}c_PVT_SUPPORT{suffix}"] = data["Support"]
    # buy_sell_signals[f"{prefix}c_PVT_RESISTANCE{suffix}"] = data["Resistance"]
    buy_sell_signals[f"{prefix}c_PVT_SUPPORT_BULL{suffix}"] = data["SUPPORT_BULL"]
    buy_sell_signals[f"{prefix}c_PVT_SUPPORT_BEAR{suffix}"] = data["SUPPORT_BEAR"]
    # buy_sell_signals[f"{prefix}c_PVT_DIVERGENCE_BULL{suffix}"] = data[
    #     "PVT_DIVERGENCE_BULL"
    # ]
    # buy_sell_signals[f"{prefix}c_PVT_DIVERGENCE_BEAR{suffix}"] = data[
    #     "PVT_DIVERGENCE_BEAR"
    # ]
    # # buy_sell_signals = pd.concat([buy_sell_signals, data], axis=1)
    del data
    gc.collect()

    return buy_sell_signals


default_pvt_signal = "c_PVT_SUPPORT_BULL"
pvt_grid_of_parameter = parameter_grid({"drift": [1, 2, 3, 4, 5], "sma_length": [3, 5, 7, 9, 15, 20]})
