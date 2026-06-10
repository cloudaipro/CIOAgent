import pandas as pd
import pandas_ta
import numpy as np
import gc
from indicators import find_swings, classify_swings
from strategies.ta_util import over_bought_sold_signal, detect_divergence
from parameter_grid import parameter_grid


"""
https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/
"""
def create_signals(
    df, close, prefix="", suffix="", **kwargs
):
    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    length = kwargs.get("length", 14) #int(length) if length and length > 0 else 14
    ema_length = kwargs.get("ema_length", 9)  # int(ema_length) if ema_length and ema_length > 0 else 9
    limit_delta = kwargs.get("limit_delta", 30)
    up_limit = 50 + limit_delta
    down_limit = 50 - limit_delta

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    data["close"] = df[close]
    data["rsi"] = df.ta.rsi(close=close, length=length)

    data[
        [
            "OVERBOUGHT",
            "OVERSOLD",
            "OVERBOUGHTSOLD_CSLS",
            "OVERBOUGHT_BULL",
            "OVERBOUGHT_BEAR",
            "OVERSOLD_BULL",
            "OVERSOLD_BEAR",
            "CENTRALLINE_BULL",
            "CENTRALLINE_BEAR",
        ]
    ] = over_bought_sold_signal(
        data,
        "rsi",
        overbought=up_limit,
        oversold=down_limit,
        central_line_crossover=True,
    )

    ## divergence
    data["ema"] = df.ta.ema(close=close, length=ema_length)
    data[["DIVERGENCE_BULL", "DIVERGENCE_BEAR"]] = detect_divergence(
        data["ema"], data["rsi"]
    )

    data = data.dropna()

    buy_sell_signals = pd.DataFrame(index=df.index)
    buy_sell_signals[f"{prefix}c_RSI_OVERBOUGHT{suffix}"] = data["OVERBOUGHT"]
    buy_sell_signals[f"{prefix}c_RSI_OVERSOLD{suffix}"] = data["OVERSOLD"]
    buy_sell_signals[f"{prefix}f_RSI_OVERBOUGHTSOLD_CSLS{suffix}"] = data[
        "OVERBOUGHTSOLD_CSLS"
    ]
    buy_sell_signals[f"{prefix}c_RSI_OVERBOUGHT_BULL{suffix}"] = data["OVERBOUGHT_BULL"]
    buy_sell_signals[f"{prefix}c_RSI_OVERBOUGHT_BEAR{suffix}"] = data["OVERBOUGHT_BEAR"]
    buy_sell_signals[f"{prefix}c_RSI_OVERSOLD_BULL{suffix}"] = data["OVERSOLD_BULL"]
    buy_sell_signals[f"{prefix}c_RSI_OVERSOLD_BEAR{suffix}"] = data["OVERSOLD_BEAR"]
    buy_sell_signals[f"{prefix}c_RSI_CENTRALLINE_BULL{suffix}"] = data[
        "CENTRALLINE_BULL"
    ]
    buy_sell_signals[f"{prefix}c_RSI_CENTRALLINE_BEAR{suffix}"] = data[
        "CENTRALLINE_BEAR"
    ]
    buy_sell_signals[f"{prefix}c_RSI_DIVERGENCE_BULL{suffix}"] = data["DIVERGENCE_BULL"]
    buy_sell_signals[f"{prefix}c_RSI_DIVERGENCE_BEAR{suffix}"] = data["DIVERGENCE_BEAR"]
    del data
    gc.collect()

    return buy_sell_signals

default_rsi_signal = "c_RSI_OVERBOUGHT_BULL"
rsi_grid_of_parameter = parameter_grid({"length": [5, 9, 14, 20], "limit_delta": [10, 20, 25, 30]})

# div_parameters_search_space = {
#     "length": [5, 9, 14, 20],
#     "limit_delta": [10, 20, 25, 30],
#     "ema_length": [3, 5, 7, 9, 15, 20],
# }
# div_multi_index = pd.MultiIndex.from_product(
#     [
#         div_parameters_search_space["length"],
#         div_parameters_search_space["limit_delta"],
#         div_parameters_search_space["ema_length"],
#         metrics,
#     ],
#     names=list(div_parameters_search_space.keys()) + ["metric"],
# )

# def find_best_div_parameters(
#     symbol, df, close, signal_column="c_RSI_OVERBOUGHT_BEAR", **kwargs
# ):
#     log_values = pd.Series(index=div_multi_index)

#     best_score = {
#         "precision": 0,
#         "recall": 0,
#         "f1": 0,
#     }
#     parameters_for_best_score = {"length": 0, "limit_delta": 0, "ema_length": 0}
#     for length in div_parameters_search_space["length"]:
#         for limit_delta in div_parameters_search_space["limit_delta"]:
#             for ema_length in div_parameters_search_space["ema_length"]:
#                 precision, recall, f1 = get_score(
#                     df,
#                     close=close,
#                     length=length,
#                     ema_length=ema_length,
#                     signal_column=signal_column,
#                     limit_delta=limit_delta
#                 )
#                 log_values[length, limit_delta, ema_length, "precision"] = precision
#                 log_values[length, limit_delta, ema_length, "recall"] = recall
#                 log_values[length, limit_delta, ema_length, "f1"] = f1
#                 if f1 > best_score["f1"]:
#                     best_score["f1"] = f1
#                     best_score["precision"] = precision
#                     best_score["recall"] = recall
#                     parameters_for_best_score["length"] = length
#                     parameters_for_best_score["ema_length"] = ema_length
#                     parameters_for_best_score["limit_delta"] = limit_delta

#     if parameters_for_best_score["length"] == 0:
#         parameters_for_best_score = None

#     if "vervose" in kwargs and kwargs["vervose"]:
#         print(log_values)

#     return best_score, parameters_for_best_score, log_values
