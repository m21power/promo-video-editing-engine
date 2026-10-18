"""Typed settings container (any key in config.DEFAULTS becomes an attribute)."""
import config


class Settings:
    def __init__(self, **values):
        for key, default in config.DEFAULTS.items():
            v = values.get(key, default)
            try:
                if isinstance(default, bool):
                    v = bool(v)
                elif isinstance(default, int):
                    v = int(round(float(v)))
                elif isinstance(default, float):
                    v = float(v)
                else:
                    v = str(v)
            except (TypeError, ValueError):
                v = default
            setattr(self, key, v)

    def to_dict(self):
        return {k: getattr(self, k) for k in config.DEFAULTS}
