from pathlib import Path


def check_path(path, label):
    p = Path(path)
    return {
        "label": label,
        "ok": p.exists(),
        "detail": str(p),
    }


def check_callable(obj, name, label=None):
    ok = callable(getattr(obj, name, None))
    return {
        "label": label or name,
        "ok": ok,
        "detail": name,
    }


def check_attr(obj, name, label=None):
    ok = hasattr(obj, name)
    return {
        "label": label or name,
        "ok": ok,
        "detail": name,
    }


def summarize_checks(checks):
    total = len(checks)
    passed = sum(1 for x in checks if x.get("ok"))
    failed = total - passed
    return passed, failed, total
