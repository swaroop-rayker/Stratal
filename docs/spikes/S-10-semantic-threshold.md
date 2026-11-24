# S-10 — The semantic-duplicate threshold, measured on coco10k

| | |
|---|---|
| For | M11 kickoff K1 and K2; DATA-2.3, DATA-4.8; [ADR-0013](../decisions/ADR-0013-semantic-duplicates.md) rule 6 (the default comes with its measurement; the owner chooses, as OC-5) |
| Date | 2026-11-21 |
| Scripts | [`scripts/measure/semantic_threshold.py`](../../scripts/measure/semantic_threshold.py), [`scripts/measure/semantic_angle.py`](../../scripts/measure/semantic_angle.py), seed 0 |
| Data | [`data/semantic-threshold-coco10k.json`](data/semantic-threshold-coco10k.json), [`data/semantic-angle-coco10k.json`](data/semantic-angle-coco10k.json) |
| Model | `clip-vit-b-32-laion2b`, fp16, the label stage's CLIP; image vectors L2-normalised, similarity = cosine |
| Cost | 10,000 images encoded and 49,995,000 pairs compared in 159 s on the RTX 4050, peak 514 MB of VRAM. In the label stage the vectors already exist (LABEL-1.3), so clustering adds only the comparison: seconds |

## What was measured

1. **Planted variants** of 400 random coco10k images, each against its original: an 80 % centre crop, a random 60 % crop, a mirrored 90 % crop (DATA-2.3.AC1's "cropped variants").
2. **Synthetic "same scene, other angle" pairs** (K2): two crops of one photo from opposite corners, the second warped in perspective, at crop sides 0.7 to 0.9 and warps 0 to 12 %, with their pHash distance (8×8, as the clean stage, which joins at 8 bits).
3. **Every pair of the dataset**, split by whether the two images share their COCO category, and for each threshold the clusters that union-find (ADR-0013 rule 1) makes of the pairs at or above it.
4. **What the pairs look like**: six example pairs per band, viewed (a contact sheet made by a throwaway script; COCO's images are not copied into the repository).

## Results

### Planted variants: the share found at each threshold

| Threshold | 80 % centre crop | Mirrored 90 % crop | Random 60 % crop | Other angle: 90 % crops, 6 % warp | Other angle: 85 % crops, 6 % warp |
|---|---|---|---|---|---|
| 0.85 | 95.5 % | 95.8 % | 41.0 % | 90.5 % | 73.3 % |
| 0.88 | 87.0 % | 94.3 % | 23.0 % | 80.0 % | 52.5 % |
| **0.90** | **78.0 %** | **92.0 %** | **11.5 %** | **70.8 %** | **36.8 %** |
| 0.92 | 60.3 % | 82.5 % | 4.3 % | 53.5 % | 21.3 % |
| 0.93 | 49.5 % | 76.8 % | 2.0 % | 42.0 % | 12.5 % |
| 0.95 | 21.8 % | 58.5 % | 0.3 % | – | – |

Medians: 80 % crop 0.930, mirror 0.954, 60 % crop 0.835. A 60 % crop is often a different picture to CLIP: it keeps a third of the scene.

**pHash cannot see these pairs at all.** Every synthetic other-angle pair, and every 60 % crop, is more than 8 bits apart (median 30 to 32 of 64), even with 90 % crops: a shift moves every DCT coefficient. This is the gap DATA-4.8 exists for, and it gives K2's test pair: **two 90 % crops from opposite corners, the second warped 6 %**, are far by pHash in every case and close by CLIP (median 0.924).

Too harsh, for the record: 70 % crops share 16 % of the image and gave a median of 0.73, more a different photo than another angle.

### The dataset's own pairs

| Threshold | Pairs at or above | Same category | Clusters | Rows in clusters | Largest cluster |
|---|---|---|---|---|---|
| 0.80 | 15,408 | 85 % | 328 | 3,182 | **572** |
| 0.85 | 3,025 | 90 % | 254 | 1,386 | **260** |
| 0.88 | 757 | 92 % | 168 | 631 | 55 |
| **0.90** | **250** | **95 %** | **105** | **290** | **22** |
| 0.92 | 67 | 96 % | 43 | 107 | 12 |
| 0.93 | 33 | 94 % | 29 | 62 | 4 |
| 0.95 | 10 | 90 % | 10 | 20 | 2 |

Below 0.88 union-find chains look-alikes into large groups: at 0.85 one cluster holds 260 images (2.6 % of the dataset), which would sit wholly in one split and pull the split's proportions (DATA-4.1). From 0.90 up the largest cluster is 22 rows or fewer.

### What the pairs look like (viewed)

- **0.95 and above: the same scene.** The same moment shot twice (a cow show, a baseball pitch, a black cat in a sink), the same train at the same platform, a crop of one photo (a clock tower).
- **0.92 to 0.95: mostly the same scene** (the same skiers, the same giraffe enclosure, the same pizza), with some look-alikes (two different trains at similar platforms).
- **0.90 to 0.92: mostly look-alikes**: different photos of zebras on a savanna, giraffes, trains in stations. Same category, not the same scene.

## The trade-off

With the default `semantic_action: mark` (ADR-0013 rule 3) a cluster only keeps its rows on one side of the split; nothing is removed. Then:

- **A missed duplicate leaks**: two photos of one moment can land in train and test, and the test score flatters the model. This is what DATA-4.8 is for.
- **A false merge costs little**: two look-alikes share a split, and the split is a little coarser. It only harms when clusters grow large enough to skew the proportions, which happens below 0.88.

With `semantic_action: remove` the trade-off reverses: a false merge deletes a useful, different photo, so a remove run wants 0.95 or above.

## Recommendation

**`semantic_threshold: 0.90`** as the default.

- It finds 78 % of 80 % crops, 92 % of mirrored crops and 71 % of the realistic other-angle pairs.
- It keeps clusters small: 290 rows in 105 clusters, the largest 22 rows (0.2 % of coco10k).
- Its extra pairs over 0.92 are look-alikes of one category, which under `mark` only share a split.

The alternatives, if you weigh leakage and look-alikes differently:

- **0.92**: mostly same-scene pairs only (107 rows, largest 12), at the cost of missing 40 % of 80 % crops and about half of the other-angle pairs.
- **0.88**: catches more (87 % of 80 % crops), with clusters up to 55 rows and more look-alikes.

I would not go below 0.88 (chaining) and would note in the config's help that a `remove` run should use 0.95 or above.

## Consequences for the build

- `LabelConfig.semantic_threshold` defaults to the chosen value; the label stage's help text states the measurement.
- `test_DATA_2_3_*` plants an 80 % centre crop and a mirrored 90 % crop of an image whose pair is measured at or above the threshold with the real CLIP (GPU test), and checks the report; unit tests use fixed vectors.
- `test_DATA_4_8_*` uses K2's pair: two 90 % crops from opposite corners, the second warped 6 %, checked to be over 8 pHash bits apart and at or above the threshold, which must land in one split.
