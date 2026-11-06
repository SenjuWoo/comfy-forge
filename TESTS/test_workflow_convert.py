"""The shipped workflow must convert to an executable graph, exactly as shipped.

Guards the failure mode this repo had: a test graph that looked like the workflow
but used different weights, a bypassed saver, and a different loader class.
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from workflow_to_api import convert  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(REPO, "workflows", "anima-4way-merge.json")

fail = 0


def check(cond, name):
    global fail
    if cond:
        print("ok   " + name)
    else:
        print("FAIL " + name)
        fail += 1


workflow = json.load(open(WF, encoding="utf-8"))
prompt, skipped = convert(workflow)

classes = [v["class_type"] for v in prompt.values()]
check("HaDeSAnimaModelSaver" in classes, "the shipped workflow contains a saver")
check("HaDeSAnimaMultiModelMerger" in classes, "the shipped workflow contains the merger")

saver = next(v for v in prompt.values() if v["class_type"] == "HaDeSAnimaModelSaver")
check(saver["inputs"]["save_format"].startswith("Native"),
      "the saver writes a native ComfyUI checkpoint")
check(saver["inputs"]["filename_prefix"], "the saver has a filename prefix")

merger = next(v for v in prompt.values() if v["class_type"] == "HaDeSAnimaMultiModelMerger")
weights = [merger["inputs"][k] for k in ("weight_1", "weight_2", "weight_3", "weight_4")]
check(weights == [0.70, 0.90, 0.55, 0.45], "the shipped weights are the documented recipe")
check(abs(merger["inputs"]["scale"] - 0.30) < 1e-9, "the shipped scale is 0.30")
check(merger["inputs"]["method"] == "Task Arithmetic", "the shipped method is task arithmetic")
check(["155", 0] == merger["inputs"]["base_model"], "the base model is linked from the base loader")
for k in ("model_1", "model_2", "model_3", "model_4"):
    check(isinstance(merger["inputs"][k], list), "merger.%s is wired to a loader" % k)

ks = next(v for v in prompt.values() if v["class_type"] == "HaDeSAnimaKSampler")
check(ks["inputs"]["scheduler"] == "anima_beta57", "the workflow keeps its beta57 scheduler")

loaders = [v for v in prompt.values() if v["class_type"] == "HaDeSAnimaModelLoader"]
check(len(loaders) == 5, "four donors plus one base loader")
check(all(v["inputs"].get("unet_name") for v in loaders), "every loader names a model")

# A saver that is bypassed would make the workflow produce nothing to save.
raw = json.load(open(WF, encoding="utf-8"))
check(all(n["mode"] == 0 for n in raw["nodes"] if n["type"] == "HaDeSAnimaModelSaver"),
      "the saver is enabled by default in the UI file")

# Drift guards: a mismatched widget count or unknown class must fail loudly.
bad = copy.deepcopy(raw)
for n in bad["nodes"]:
    if n["type"] == "HaDeSAnimaModelSaver":
        n["widgets_values"] = n["widgets_values"][:3]
try:
    convert(bad)
    check(False, "a truncated widget list is rejected")
except SystemExit as e:
    check("widget values" in str(e), "a truncated widget list is rejected")

bad2 = copy.deepcopy(raw)
bad2["nodes"][0]["type"] = "SomeUnknownNode"
try:
    convert(bad2)
    check(False, "an unknown node class is rejected")
except SystemExit as e:
    check("unknown class" in str(e), "an unknown node class is rejected")

if fail == 0:
    print("WORKFLOW CONVERT: PASS")
    sys.exit(0)
print("WORKFLOW CONVERT: %d FAILED" % fail)
sys.exit(1)
