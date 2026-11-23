"""Reading a field off a Google Ad Manager object."""


def gam_field(obj: object, *path: str, default: object = None) -> object:
    """The value at *path* below *obj*, or *default* when a step of it is missing or None.

    GAM answers with Zeep objects, read by attribute; the payloads this adapter builds, and the
    stand-ins tests hand it, are dicts read by key. One reader serves both.
    """
    for key in path:
        if obj is None:
            return default
        obj = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
    return default if obj is None else obj
