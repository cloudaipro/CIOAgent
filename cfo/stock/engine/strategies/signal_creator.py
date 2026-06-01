from strategies import *
class signal_creator:
    def __init__(self, open="Open", high="High", low="Low", close="Close", volume="Volume"):
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.creators = {
            "adobv": lambda df, parameters: adobv_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                **parameters,
            ),
            "adosc": lambda df, parameters: adosc_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                **parameters,
            ),
            "awesome": lambda df, parameters: awesome_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                **parameters,
            ),
            "bop": lambda df, parameters: bop_strategy.create_signals(
                df,
                open=self.open,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "cci": lambda df, parameters: cci_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "cfo": lambda df, parameters: cfo_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "cg": lambda df, parameters: cg_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "cmf": lambda df, parameters: cmf_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                **parameters,
            ),
            "cmo": lambda df, parameters: cmo_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "coppock": lambda df, parameters: coppock_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "cti": lambda df, parameters: cti_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "efi": lambda df, parameters: efi_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                **parameters,
            ),
            "er": lambda df, parameters: er_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "eri": lambda df, parameters: eri_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "inertia": lambda df, parameters: inertia_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "kdj": lambda df, parameters: kdj_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "kst": lambda df, parameters: kst_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "kvo": lambda df, parameters: kvo_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                volume=self.volume,
                **parameters,
            ),
            "macd": lambda df, parameters: macd_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "pgo": lambda df, parameters: pgo_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "ppo": lambda df, parameters: ppo_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "psl": lambda df, parameters: psl_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "pvo": lambda df, parameters: pvo_strategy.create_signals(
                df,
                volume=self.volume,
                close=self.close,
                **parameters,
            ),
            "pvt": lambda df, parameters: pvt_strategy.create_signals(
                df,
                close=self.close,
                volume=self.volume,
                **parameters,
            ),
            "qqe": lambda df, parameters: qqe_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "rsi": lambda df, parameters: rsi_strategy.create_signals(
                df, close=self.close, **parameters
            ),
            "rsx": lambda df, parameters: rsx_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "rvgi": lambda df, parameters: rvgi_strategy.create_signals(
                df,
                open=self.open,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "rvi": lambda df, parameters: rvi_strategy.create_signals(
                df, close=self.close, high=self.high, low=self.low, **parameters
            ),
            "stc": lambda df, parameters: stc_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "stoch": lambda df, parameters: stoch_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "squeeze": lambda df, parameters: squeeze_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "tdseq": lambda df, parameters: tdseq_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "trix": lambda df, parameters: trix_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "tsi": lambda df, parameters: tsi_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "uo": lambda df, parameters: uo_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
            "vidya": lambda df, parameters: vidya_strategy.create_signals(
                df,
                close=self.close,
                **parameters,
            ),
            "willr": lambda df, parameters: willr_strategy.create_signals(
                df,
                high=self.high,
                low=self.low,
                close=self.close,
                **parameters,
            ),
        }

    def create_signals(self, df, strategy, **parameters):
        return self.creators[strategy](df, parameters)
