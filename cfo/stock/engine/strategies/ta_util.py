import pandas as pd
from indicators import candles_between_crosses, rolling_signal_list
import numpy as np
from indicators import find_data_swings, classify_swings
import gc

def candles_between_signed(data, initial_count=1):
    """
    Calculates the number of candles between sign changes in the given data.

    Parameters:
    - data: A pandas Series or DataFrame containing the data.
    - initial_count: An integer representing the initial count of candles.

    Returns:
    - A pandas DataFrame with a single column named 'CSLS' representing the number of candles between sign changes.
    """
    signed = np.sign(data)
    new_level = np.where(signed != signed.shift(), 1, 0)
    return pd.DataFrame(
        {
            "CSLS": candles_between_crosses(new_level, initial_count=initial_count)
            * signed
        },
        index=data.index,
    )


def over_bought_sold_signal(
    data, column, overbought=75, oversold=25, central_line_crossover=False, central_line=50
):
    """
    Generates overbought and oversold signals based on the given data and parameters.

    Args:
        data (pd.DataFrame): The input data containing the price values.
        column (str): The column name in the data DataFrame that contains the price values.
        overbought (float, optional): The threshold value above which the signal is considered overbought. Defaults to 75.
        oversold (float, optional): The threshold value below which the signal is considered oversold. Defaults to 25.
        central_line_crossover (bool, optional): Whether to include central line crossover signals. Defaults to False.
        central_line (float, optional): The central line value for central line crossover signals. Defaults to 50.

    Returns:
        pd.DataFrame: A DataFrame containing the generated signals.

    """

    signals = pd.DataFrame(index=data.index)
    p_line = data[column].shift(1)
    signals["OVERBOUGHT"] = 0
    signals["OVERSOLD"] = 0
    signals.loc[data[column] > overbought, "OVERBOUGHT"] = 1
    signals.loc[data[column] < oversold, "OVERSOLD"] = 1
    signals["merge"] = signals.OVERBOUGHT - signals.OVERSOLD
    signals["new_level"] = np.where(signals["merge"] != signals["merge"].shift(1), 1, 0)
    signals["OVERBOUGHTSOLD_CSLS"] = (
        candles_between_crosses(signals["new_level"], initial_count=1)
        * signals["merge"]
    )
    signals["OVERBOUGHT_BULL"] = 0  # exit overbought
    signals["OVERBOUGHT_BEAR"] = 0  # exit overbought
    signals["OVERSOLD_BULL"] = 0  # exit oversold
    signals["OVERSOLD_BEAR"] = 0  # exit oversold
    signals.loc[
        (data[column] > overbought) & (p_line < overbought), "OVERBOUGHT_BULL"
    ] = 1
    signals.loc[
        (data[column] < overbought) & (p_line > overbought), "OVERBOUGHT_BEAR"
    ] = 1

    signals.loc[(data[column] > oversold) & (p_line < oversold), "OVERSOLD_BULL"] = 1
    signals.loc[(data[column] < oversold) & (p_line > oversold), "OVERSOLD_BEAR"] = 1

    if central_line_crossover:
        signals["CENTRALLINE_BULL"] = 0
        signals["CENTRALLINE_BEAR"] = 0
        signals.loc[
            (data[column] > central_line) & (p_line < central_line),
            "CENTRALLINE_BULL",
        ] = 1
        signals.loc[
            (data[column] < central_line) & (p_line > central_line),
            "CENTRALLINE_BEAR",
        ] = 1

    del p_line
    if central_line_crossover:
        return signals[
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
        ]
    else:
        return signals[
            [
                "OVERBOUGHT",
                "OVERSOLD",
                "OVERBOUGHTSOLD_CSLS",
                "OVERBOUGHT_BULL",
                "OVERBOUGHT_BEAR",
                "OVERSOLD_BULL",
                "OVERSOLD_BEAR",
            ]
        ]


def crossover_signal(data, column1, column2, zero_line_crossover=False):
    """
    Generates crossover signals based on two columns of data.

    Args:
        data (pandas.DataFrame): The input data containing the columns for analysis.
        column1 (str): The name of the first column to compare.
        column2 (str): The name of the second column to compare.
        zero_line_crossover (bool, optional): Whether to include zero line crossover signals. 
            Defaults to False.

    Returns:
        pandas.DataFrame: A DataFrame containing the generated crossover signals.

    """
    signals = pd.DataFrame(index=data.index)
    signals["CROSSOVER_BULL"] = 0
    signals["CROSSOVER_BEAR"] = 0
    line1 = data[column1]
    line2 = data[column2]
    p_line1 = line1.shift(1)
    p_line2 = line2.shift(1)
    signals.loc[
        (line1 > line2) & (p_line1 < p_line2),
        "CROSSOVER_BULL",
    ] = 1
    signals.loc[
        (line1 < line2) & (p_line1 > p_line2),
        "CROSSOVER_BEAR",
    ] = 1
    signals["merged"] = signals["CROSSOVER_BULL"] - signals["CROSSOVER_BEAR"]
    signals["CSLS"] = candles_between_crosses(
        signals["merged"], initial_count=1
    )
    signals["CROSSOVER_CSLS"] = (
        rolling_signal_list(signals["merged"]) * signals["CSLS"]
    )

    if zero_line_crossover:
        signals["ZEROCROSS_BULL"] = 0
        signals["ZEROCROSS_BEAR"] = 0
        signals.loc[
            (line1 > 0) & (p_line1 < 0),
            "ZEROCROSS_BULL",
        ] = 1
        signals.loc[
            (line1 < 0) & (p_line1 > 0),
            "ZEROCROSS_BEAR",
        ] = 1

    del line1, line2, p_line1, p_line2

    if zero_line_crossover:
        return signals[
            [
                "CROSSOVER_BULL",
                "CROSSOVER_BEAR",
                "CROSSOVER_CSLS",
                "ZEROCROSS_BULL",
                "ZEROCROSS_BEAR",
            ]
        ]
    else:
        return signals[["CROSSOVER_BULL", "CROSSOVER_BEAR", "CROSSOVER_CSLS"]]


def conditional_crossover_signal(data, column1, column2, level):
    """
    Generate conditional crossover signals based on the given data, columns, and level.

    Parameters:
    - data (pandas.DataFrame): The input data containing the columns for comparison.
    - column1 (str): The name of the first column to compare.
    - column2 (str): The name of the second column to compare.
    - level (float): The threshold level for the crossover condition.

    Returns:
    - signals (pandas.DataFrame): A DataFrame containing the generated signals.
      - "BULL" column: Binary values indicating bullish signals.
      - "BEAR" column: Binary values indicating bearish signals.
    """
    signals = pd.DataFrame(index=data.index)
    signals["CROSSOVER_BULL"] = 0
    signals["CROSSOVER_BEAR"] = 0
    line1 = data[column1]
    line2 = data[column2]
    p_line1 = line1.shift(1)
    p_line2 = line2.shift(1)

    signals.loc[
        (line1 > line2) & (p_line1 < p_line2) & (line1 < level) & (line2 < level),
        "CROSSOVER_BULL",
    ] = 1
    signals.loc[
        (line1 < line2) & (p_line1 > p_line2) & (line1 > level) & (line2 > level),
        "CROSSOVER_BEAR",
    ] = 1

    del line1, line2, p_line1, p_line2
    return signals[["CROSSOVER_BULL", "CROSSOVER_BEAR"]]


def detect_divergence(line1, line2, tol: int = 3, prefix="", suffix=""):
    """
    Detects divergences between two lines.

    Args:
        line1 (pd.Series): The first line.
        line2 (pd.Series): The second line.
        tol (int, optional): Tolerance level for detecting divergences. Defaults to 3.
        prefix (str, optional): Prefix to be added to the column names of the divergence signals. Defaults to "".
        suffix (str, optional): Suffix to be added to the column names of the divergence signals. Defaults to "".

    Returns:
        pd.DataFrame: A DataFrame containing the divergence signals.
    """

    # If prefix or suffix is provided, adjust them
    if len(prefix) > 0:
        prefix = prefix + "_"
    if len(suffix) > 0:
        suffix = "_" + suffix

    data = pd.DataFrame(index=line1.index)
    line1_swings = classify_swings(find_data_swings(line1))
    line2_swings = classify_swings(find_data_swings(line2))
    csls1 = line1_swings.Trend * line1_swings.CSLS
    csls2 = line2_swings.Trend * line2_swings.CSLS
    data["DIVERGENCE_BULL"] = 0
    data["DIVERGENCE_BEAR"] = 0
    data.loc[(csls1 < -tol) & (csls2 > tol),"DIVERGENCE_BULL"] = 1
    data.loc[(csls1 > tol) & (csls2 < -tol), "DIVERGENCE_BEAR"] = 1

    divergence_signals = pd.DataFrame(index=line1.index)
    divergence_signals[f"{prefix}c_DIVERGENCE_BULL{suffix}"] = data["DIVERGENCE_BULL"]
    divergence_signals[f"{prefix}c_DIVERGENCE_BEAR{suffix}"] = data["DIVERGENCE_BEAR"]

    del data
    gc.collect()
    return divergence_signals


def detect_consolidation(df, high, low, close, volume, length=10, std=2.0, scalar=1.2):
    """
    Detects consolidation patterns in stock price data.

    Args:
        df (pandas.DataFrame): The input DataFrame containing stock price data.
        high (str): The column name for the high prices.
        low (str): The column name for the low prices.
        close (str): The column name for the closing prices.
        volume (str): The column name for the volume data.
        length (int, optional): The window length for calculating moving averages and standard deviations. Defaults to 10.
        std (float, optional): The number of standard deviations to use for calculating Bollinger Bands. Defaults to 2.0.
        scalar (float, optional): The scalar value to use for calculating Keltner Channels. Defaults to 1.2.

    Returns:
        pandas.DataFrame: A DataFrame containing the detected consolidation patterns and the corresponding CSLS values.
    """
    # Create a DataFrame to store calculated values
    data = pd.DataFrame(index=df.index)

    data["SMA"] = df[close].rolling(window=length).mean()
    data["stdev"] = df[close].rolling(window=length).std()
    data["Lower_Bollinger"] = data["SMA"] - (std * data["stdev"])
    data["Upper_Bollinger"] = data["SMA"] + (std * data["stdev"])
    data["TR"] = abs(df[high] - df[low])
    data["ATR"] = data["TR"].rolling(window=length).mean()
    data["Upper_KC"] = data["SMA"] + (scalar * data["ATR"])
    data["Lower_KC"] = data["SMA"] - (scalar * data["ATR"])
    data[volume] = df[volume]
    data["MeanVolume"] = df[volume].rolling(window=length).mean()
    data["LowVolume"] = data[volume] < data["MeanVolume"]
    data["consolidation"] = 0
    data.loc[
        (data["Lower_Bollinger"] > data["Lower_KC"])
        & (data["Upper_Bollinger"] < data["Upper_KC"]),  # & (data["LowVolume"])
        "consolidation",
    ] = 1
    new_level = np.where(
        data.consolidation != data.consolidation.shift(),
        1,
        0,
    )
    # Add column 'consolidation since last swing' CSLS
    data["CSLS"] = candles_between_crosses(new_level, initial_count=1)
    # data.loc[~(data["consolidation"] > 0), "CSLS"] = 0

    consolidation_data = pd.DataFrame(
        index=df.index, data=data.dropna()[["consolidation", "CSLS"]]
    )

    return consolidation_data
