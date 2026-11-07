"""Consistency signal: the share of an instance's views whose own top label agrees with its class.

A view agrees with a class if its best label is that class; with "unclassified" if its best
label is not an asset class. One confident view among doubtful ones scores low.
"""
import numpy as np

from appearance import CLASSES, LABELS
from verdict import Signal


def judge(view_probabilities, name):
    tops = [LABELS[int(np.argmax(p))] for p in view_probabilities]
    agree = sum(t == name if name in CLASSES else t not in CLASSES for t in tops)
    share = agree / max(len(tops), 1)
    return Signal("agree" if share >= 0.5 else "disagree", share, f"{agree}/{len(tops)} views agree")
