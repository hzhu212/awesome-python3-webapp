import config_default


class MyDict(dict):
    """Simple dict but surpport access as x.y style"""
    def __init__(self, names=(), values=(), **kw):
        super(MyDict, self).__init__(**kw)
        for k, v in zip(names, values):
            self[k] = v

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError('"Dict" object has no attribute "%s"' % key)

    def __setattr__(self, k, v):
        self[k] = v

    @staticmethod
    def from_dict(d):
        res = MyDict()
        for k, v in d.items():
            res[k] = v if not isinstance(v, dict) else MyDict.from_dict(v)
        return res


def merge(default, override):
    res = {}
    for k, v in default.items():
        if k in override:
            if isinstance(v, dict):
                res[k] = merge(v, override[k])
            else:
                res[k] = override[k]
        else:
            res[k] = v
    return res


# def toMyDict(d):
#     res = MyDict()
#     for k, v in d.items():
#         res[k] = v if not isinstance(v, dict) else toMyDict(v)
#     return res



configs = config_default.configs

try:
    import config_override
    configs = merge(configs, config_override.configs)
except ImportError:
    pass

configs = MyDict.from_dict(configs)
