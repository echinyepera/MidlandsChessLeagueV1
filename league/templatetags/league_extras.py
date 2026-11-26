from django import template

from ..services import fmt_pts

register = template.Library()


@register.filter
def pts(value):
    """4.0 -> 4, 4.5 -> 4.5"""
    return "" if value is None else fmt_pts(value)


@register.filter
def res(code):
    return {"1-0": "1 – 0", "0-1": "0 – 1", "0.5-0.5": "½ – ½"}.get(code, code)
