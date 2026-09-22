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
