# Measured results

Everything here is from a live run on `pbmc3k` with Claude and Jev both reachable
through the Vercel AI Gateway. Reproduce with the scripts in `experiments/`.

Ground truth is the author's `louvain` labels from `pbmc3k_processed`, joined by
barcode. No label touches the pipeline itself.

---

## Headline

| annotator | matched accuracy |
|---|---|
| linear probe on 50 PCs (**the free baseline**) | **0.9457** |
| `jev_markers`, per cluster, with marker filtering + evidence loop | 0.848 |
| `celltypist` (`Immune_All_Low`, majority voting) | 0.799 |
| `jev_markers`, before those two fixes | 0.718 |

**The free baseline still wins by ~10 points.** Logistic regression on the PCA
embedding classifies cell type better than any annotation route we built. That is
the number to beat and it is not beaten. The orchestrated route closed more than
half the gap in one afternoon, and the remaining gap is concentrated in a single
cluster whose failure is diagnosed below.

> **Correction (Experiment 3):** matched accuracy cannot rank per-cluster
> annotators. Its one-to-one map gives full credit to a cluster that is
> consistently given the *wrong* name, so every arm in Experiment 3 ties at
> 0.848, even the arm that calls 437 CD8 T cells "NK cell". The 0.848 above
> is that tie, not a result. Experiment 3 reports cell accuracy in a shared
> vocabulary instead.

Matched accuracy solves the assignment problem between predicted and true label
sets, so it does not punish an annotator for using a different vocabulary.
Exact-match accuracy for the same `celltypist` run is **0.1319** — that number
measures vocabulary agreement, not biology, and is reported only as a diagnostic.

---

## Experiment 1: are the decisions worth making, and does Jev make them well?

Every arm run with all other decisions pinned; winner by hindsight ARI.

| decision | arms | winner | ARI spread | Jev said | conf | right? |
|---|---|---|---|---|---|---|
| `normalize.method` | log1p_cpm / pearson_residuals / median_size_factors | log1p_cpm | 0.167 | log1p_cpm | 0.92 | yes |
| `features.n_hvg` | 1000 / 2000 / 4000 | **1000** | 0.178 | 2000 | **0.98** | no |
| `cluster.resolution` | 0.4 / 1.0 / 1.6 | **0.4** | **0.425** | 1.0 | 0.95 | no |

Two findings.

**Jev reproduces the scanpy tutorial defaults, three for three.** `log1p_cpm`,
`2000`, `1.0` are the conventional settings, and they were optimal once out of
three. Its calibration target is what a practitioner would say, not what
maximises this dataset's outcome; those objectives diverge.

**Confidence is orthogonal to consequence, and here ran against correctness** —
0.98 and 0.95 on the two misses, 0.92 on the hit. A calibrated model reports
P(right); it never reports cost(wrong). Expected-value routing needs both terms
and only one of them comes from the decision model.

### Caveats that limit this

- "Winner" is hindsight ARI against labels unavailable at runtime. This measures
  whether Jev matches hindsight, not whether it is usable in a real run.
- ARI rewards partitions with roughly the right number of clusters, and pbmc3k
  has 8 true types, so the resolution result is largely the metric preferring
  fewer clusters. It is the weakest of the three.
- `features.n_hvg` is the clean one: ARI (0.779 vs 0.668) and matched accuracy
  (0.842 vs 0.799) agree that 1000 beats 2000, while the linear probe disagrees
  (0.922 vs 0.946). **The winner is objective-dependent and nobody supplied an
  objective.**
- n = 1 dataset, 1 run per arm. No repeats.

---

## Experiment 1b: was the resolution ballot in the right place?

Experiment 1 found `0.4` beating Jev's `1.0` and flagged two limits: ARI may
simply prefer fewer clusters, and n = 1 dataset. Both are addressed here by
sweeping a finer grid on two datasets and reporting NMI alongside ARI.
`experiments/resolution_sweep.py`, everything upstream of `cluster` computed once
per dataset.

| resolution | pbmc3k clusters | pbmc3k ARI | pbmc3k NMI | 68k clusters | 68k ARI | 68k NMI |
|---|---|---|---|---|---|---|
| 0.4 | 6 | 0.8271 | 0.8237 | 6 | 0.4334 | 0.6201 |
| 0.6 | 7 | 0.8286 | 0.8245 | 7 | 0.4370 | 0.6357 |
| **0.8** | 8 | **0.8704** | **0.8513** | 8 | **0.5010** | **0.6610** |
| 1.0 | 9 | 0.6679 | 0.7847 | 10 | 0.4110 | 0.6296 |
| 1.2 | 10 | 0.6215 | 0.7774 | 11 | 0.3809 | 0.6156 |
| 1.6 | 14 | 0.4016 | 0.6840 | 12 | 0.3846 | 0.6040 |
| 2.0 | 21 | 0.2925 | 0.6196 | 12 | 0.3807 | 0.6044 |

pbmc3k ground truth is the author's `louvain` labels; `pbmc68k_reduced` uses
`bulk_labels`, which come from bulk-sorted populations and are therefore not
another pipeline's output.

**The old ladder could not reach the best answer on either dataset.** Rungs were
`0.4 / 1.0 / 1.6`; the optimum is `0.8` on both, on both metrics. Jev's pick of
`1.0` on pbmc3k cost 0.20 ARI against a value it was never offered, and 0.16
against the best value it was. Re-centred to `0.4 / 0.8 / 1.2`.

**The "ARI just prefers fewer clusters" caveat does not survive.** On
`pbmc68k_reduced` the truth has 10 types, and resolution 1.0 produces exactly 10
clusters — and scores *worse* (0.4110) than the 8-cluster partition at 0.8
(0.5010). Matching the true cluster count is not what ARI is rewarding here. NMI,
which does not share ARI's preference, peaks at the same rung on both datasets.

**What this does not establish.** Both datasets are PBMC, so this is two readings
of one tissue, not two independent confirmations. The optimum is a property of
the data and there is no reason 0.8 transfers to a tumour or a developmental
series. The claim that holds is the weaker and more useful one: *the previous
rungs were placed badly*, and a ballot that cannot contain the right answer caps
every decision procedure that reads it — which is a limit on the option set, not
on the decision model.

---

## Experiment 2: per-cluster annotation and what abstention buys

One Jev call per cluster over a fixed vocabulary. 8 of 9 clusters correct.

| floor | coverage | matched accuracy |
|---|---|---|
| <= 0.60 | 1.000 | 0.848 |
| 0.70 | 0.536 | 0.855 |
| >= 0.80 | 0.534 | 0.856 |

**Giving up 46% of cells buys 0.7 accuracy points.** Abstention only pays when
confidence separates correct from incorrect answers, and here it does not: the
one wrong cluster sits at 0.64 and a correct one at 0.65. A threshold discards
them together. A flat risk-coverage curve is a measurement that the confidence is
not discriminative at this operating range, not a formality.

### What the two fixes did

`cluster 0`, 805 cells, 30% of the dataset:

```
before   RPS12, RPS27, RPS6, RPS25, RPL32   ->  Unclear @ 0.99
after    LDHB, TPT1, CD3D, CD3E, NPM1       ->  CD4 T cell @ 0.65   correct
```

`CD3D` and `CD3E` were in the ranked list all along, crowded out of the top five
by ribosomal genes. Jev declining the first version was the **correct** answer to
an unanswerable question — the defect was that differential expression against
all other clusters hands the largest cluster its housekeeping genes. Recovered by
deleting noise from the question, not by changing the model.

`cluster 3` was rescued by the abstention-triggered evidence loop: it abstained,
the loop fetched a canonical lineage panel for that cluster, and the second call
returned CD8 T cell @ 0.73, correct. One retry, on the one cluster that asked for
it.

### The loop's trigger is wrong

`cluster 5` (NKG7, CST7, GZMA, CTSW, B2M) is still called NK cell when the truth
is CD8 T cells, and **the loop never fired** because 0.64 clears the 0.55 floor.
That is the one cluster where CD3 evidence would have decided it.

Absolute confidence is the wrong trigger. "Torn between two types" is a small
**margin between the top two probabilities**, not a low absolute confidence.

### Jev is not deterministic

`cluster 5` returned 0.70 on one run and 0.64 on the next from identical markers.
Worth knowing before making any calibration claim.

---

## Experiment 3: Claude vs Jev vs CellTypist on one annotation task

`experiments/head_to_head.py`. Every arm names the same 9 Leiden clusters from
the same top-10 markers and the same closed list of 8 options. Model arms were
repeated 3×. Accuracy is **cell accuracy in the shared vocabulary**: the truth
is projected onto the list, so both monocyte populations become "Monocyte".
CellTypist's own labels are translated by fixed keyword rules that never read
the truth.

| arm | cell acc | clusters right | $ per run | wall s | median call |
|---|---|---|---|---|---|
| oracle (majority true type, the ceiling) | 0.9052 | 9/9 | — | — | — |
| `claude-sonnet-5` | **0.9045** | 8/9 | 0.0149 | 16.3 | 1.41 s |
| `claude-haiku-4-5` | **0.9045** | 8/9 | 0.0076 | 13.2 | 0.93 s |
| `jev_loop` (production route, evidence loop) | 0.8685 | 8/9 | 0.00032 | 2.3 | 0.21 s |
| `jev` (one call per cluster) | 0.8677 | 7/9 | 0.00026 | 2.1 | 0.22 s |
| `celltypist` (per cluster, majority vote) | 0.8677 | 7/9 | 0 (CPU) | 1.3 | — |
| `overlap` (marker-set intersection, no model) | 0.8677 | 7/9 | 0 | 0.0 | — |
| `claude-opus-5` | **not measured** | | | | |

Every arm gave identical labels on all 3 repeats, Jev included, so the
spread is zero.

**The whole difference is one cluster.** Cluster 5, 437 cells, markers
NKG7, CST7, GZMA, CTSW, B2M, CCL5 … PRF1. Both Claude models call it CD8 T cell.
Jev, CellTypist and the marker-overlap baseline all call it NK cell. It is
mostly CD8 T cells, so Claude gets it right. Cluster 3 is 9 proliferating cells
that only the Jev loop names. It is worth 0.0008 of accuracy.

**Jev loses on accuracy and wins on cost and speed.** It costs about 47× less
than Sonnet and about 24× less than Haiku, and it is 6–7× faster per call. That
extrapolates to about $0.03 against $1.66 per 1,000 clusters. Jev did not beat
the free no-model baseline: it produced the same labels as marker overlap on
every cluster. Haiku matches Sonnet exactly at half the price, so on this task
the larger Claude model buys nothing.

**The evidence loop did not fire where it was needed.** `jev_loop` made 10 calls:
9, plus one retry on cluster 3. Cluster 5 cleared the confidence floor, so its
CD3 panel was never fetched. This is the trigger defect from Experiment 2,
now confirmed with a Claude arm that got the cluster right.

**Opus 5 has no row.** The gateway rate-limited every attempt, up to 9 retries
with backoff per call. Each Claude arm is pinned to its model and fails rather
than answering with another. The first version of this script fell back
silently, and its "claude" column was really Sonnet.

**Matched accuracy ties every arm at 0.848**, so it cannot rank them. See the
correction under the Headline.

Caveats: one dataset, 9 clusters, and the ranking rests on one cluster. List
prices, not invoices. Claude used adaptive thinking; Haiku 4.5 ran without it.

---

## Experiment 4: does the annotation difference survive into DE?

`experiments/downstream_delta.py`. For every annotator, the same one-vs-rest
Wilcoxon DE per cell type. It reuses the exact calls priced in Experiment 3, so
there are no new API calls. Each annotator's top-25 genes per type are compared
with the top-25 you get from the true labels (Jaccard). Composition is scored
as total-variation distance from the true mix. The script refuses to run if its
rebuilt clustering differs from the one Experiment 3 scored.

| arm | cell acc | composition TV | DE Jaccard mean | worst type |
|---|---|---|---|---|
| `celltypist`, **per cell** | 0.8901 | **0.049** | **0.850** | 0.67 (CD8) |
| oracle, per cluster | 0.9052 | 0.060 | 0.735 | 0.00 (NK) |
| `claude-sonnet-5` / `haiku-4-5` | 0.9045 | 0.060 | 0.735 | 0.00 (NK) |
| `jev`, `jev_loop`, `overlap`, `celltypist` per cluster | 0.868 | 0.121 | 0.735 | 0.00 (CD8) |

**Claude and Jev tie on DE, and both lose to per-cell CellTypist.** Cluster 5
holds about 316 CD8 T cells *and* most of the NK cells. Any annotator that
labels whole clusters must drop one of the two types. Claude drops NK and Jev
drops CD8. Each loses one type's DE list completely (Jaccard 0), and the means
come out identical. Even the oracle does no better. The ceiling here is set by
the clustering, not by the annotator.

CellTypist per cell is the only arm that keeps both types. It has the lowest
cell accuracy of the three but the best DE and composition. Where CellTypist per
cell and the Jev route disagree (264 cells), CellTypist is right on 145, Jev on
88, and neither on 31.

**What this changes.** Per-cluster naming, by Claude or Jev, is capped by
cluster granularity. Choosing a better namer does not fix a cluster that
contains two cell types. The lever is splitting cluster 5, either with a finer
local resolution or with a CD3 panel check, before naming it.
Experiment 1b picked resolution by hindsight ARI, which prefers *fewer*
clusters, so it moves the wrong way for this.

---

## Where the decision model belongs

The two experiments disagree about Jev in a way that is itself the result.

On **tuning decisions** it reproduces convention at high confidence, convention
is usually not optimal here, and the confidence carries no signal about
correctness or consequence.

On the **semantic decision** — which cell type is this cluster — it is good, and
its errors and abstentions are informative about what evidence is missing.

Tuning decisions have cheap *internal* criteria available at runtime: bootstrap
stability, silhouette, a knee point. Those can be looped and measured, so asking
a model for an opinion is the wrong move. Semantic decisions have no internal
criterion — there is no unsupervised statistic for "is this a B cell" — and a
genuinely closed option set. That is where a calibrated typed model earns its
place.
