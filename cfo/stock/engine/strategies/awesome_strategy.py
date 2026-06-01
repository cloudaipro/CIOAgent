import pandas as pd
import pandas_ta
import gc
from parameter_grid import parameter_grid

"""
reference: https://www.ig.com/en/trading-strategies/a-traders-guide-to-using-the-awesome-oscillator-200130#:~:text=The%20awesome%20oscillator%20saucer%20is%20a%20trading%20signal,oscillator%20saucers%20can%20be%20either%20bullish%20or%20bearish.
"""


def create_signals(df, high="High", low="Low", prefix="", suffix="", **kwargs):
    """
    Detects Awesome Oscillator saucer signals.

    Args:
    - df: DataFrame containing OHLC data.
    - high: Column name for High prices.
    - low: Column name for Low prices.
    - fast: Period for fast moving average.
    - slow: Period for slow moving average.
    - prefix: Prefix to be added to output column names.
    - suffix: Suffix to be added to output column names.

    Returns:
    - DataFrame with added columns indicating saucer signals.
    """

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    fast = kwargs.get("fast", 5)  
    slow = kwargs.get("slow", 34) 

    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)
    # Calculate Awesome Oscillator
    data["ao"] = df.ta.ao(high=high, low=low, fast=fast, slow=slow, offset=None)

    # Shift Awesome Oscillator values for comparison
    data["p_ao"] = data["ao"].shift(1)
    data["pp_ao"] = data["ao"].shift(2)

    # Determine color based on AO value comparison
    data["color"] = None
    data.loc[data["ao"] >= data["p_ao"], "color"] = "green"
    data.loc[data["ao"] < data["p_ao"], "color"] = "red"

    # Shift color values for comparison
    data["p_color"] = data["color"].shift(1)
    data["pp_color"] = data["color"].shift(2)

    # Initialize columns for saucer signals
    data["SAUCERS_TWINPEAK_BULL"] = 0
    data["SAUCERS_TWINPEAK_BEAR"] = 0

    # Bullish saucer: Above zero line, two red bars followed by one green bar
    data.loc[
        (data["ao"] > 0)
        & (data["p_ao"] > 0)
        & (data["pp_ao"] > 0)
        & (data["color"] == "green")
        & (data["p_color"] == "red")
        & (data["pp_color"] == "red"),
        "SAUCERS_TWINPEAK_BULL",
    ] = 1
    # Bearish saucer: Below zero line, two green bars followed by one red bar
    data.loc[
        (data["ao"] < 0)
        & (data["p_ao"] < 0)
        & (data["pp_ao"] < 0)
        & (data["color"] == "red")
        & (data["p_color"] == "green")
        & (data["pp_color"] == "green"),
        "SAUCERS_TWINPEAK_BEAR",
    ] = 1
    # Drop rows with NaN values
    data = data.dropna()

    # Create a new DataFrame to store saucer signals
    saucers_data = pd.DataFrame(index=df.index)
    saucers_data[f"{prefix}c_SAUCERS_TWINPEAK_BULL{suffix}"] = data[
        "SAUCERS_TWINPEAK_BULL"
    ]
    saucers_data[f"{prefix}c_SAUCERS_TWINPEAK_BEAR{suffix}"] = data[
        "SAUCERS_TWINPEAK_BEAR"
    ]

    del data
    gc.collect()

    return saucers_data


default_awesome_signal = "c_SAUCERS_TWINPEAK_BULL"
awesome_grid_of_parameter = parameter_grid(
    {
        "fast": [3, 5],
        "slow": [17, 34],
    },
    lambda grid: (grid["fast"] == 5 and grid["slow"] == 34)
    or (grid["fast"] == 3 and grid["slow"] == 17),
)
