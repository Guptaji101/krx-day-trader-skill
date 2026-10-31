"""Load a skill's script as a module: load("krx-timemachine", "timemachine")."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "common"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load(skill, name):
    path = os.path.join(ROOT, skill, name + ".py")
    spec = importlib.util.spec_from_file_location("%s_%s" % (skill.replace("-", "_"), name), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
