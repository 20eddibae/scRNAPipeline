"""Is Jev reading the rung evidence, or anchoring on the conventional value?

Re-asks Experiment 7's evidence-rich resolution question with the option keys
replaced by neutral letters, in shuffled order, so "0.8" is no longer visible.
If the pick follows the letter that hides 0.8, the number was the signal.
"""
import json, random, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from evidence_resolution import seen_question
from scrnapipeline.config import load_settings
from scrnapipeline.jev import ChoiceQ, JevDecider
from scrnapipeline.state import RunState

out = {}
for ds in sys.argv[1:]:
    rungs = json.loads(Path(f"experiments/results/evidence_resolution_{ds}.json").read_text())["rungs"]
    base = seen_question({float(k): v for k, v in rungs.items()})
    decider, picks = JevDecider(load_settings()), []
    for rep in range(5):
        keys = list(base.criteria)
        random.Random(rep).shuffle(keys)
        letter = {k: "ABCDE"[i] for i, k in enumerate(keys)}
        q = ChoiceQ(base.instructions, {letter[k]: base.criteria[k] for k in keys}, "A")
        d = decider.decide("cluster", {"r": q}, RunState("anchor", ds))["r"]
        back = {v: k for k, v in letter.items()}
        picks.append({"rung": back.get(d.value), "confidence": d.confidence})
    out[ds] = picks
    print(ds, [p["rung"] for p in picks], [round(p["confidence"] or 0, 2) for p in picks])
Path("experiments/results/resolution_anchor.json").write_text(json.dumps(out, indent=2))
