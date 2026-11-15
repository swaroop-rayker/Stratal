# DATA-4 Train/Val/Test Splits — draft v1, for the owner's review

**Status:** approved by the owner on 2026-11-15 as v1, without changes. The requirement block below is now SPEC.md §DATA-4, the schema change is in DATA_MODEL.md, and the review decisions below were taken as proposed (DECISIONS.md, M5). This file stays as the record of the reasoning and measurements. Written by the agent at the owner's request ("You draft it").

## The requirements (proposed text for SPEC.md)

#### DATA-4 Train/Val/Test Splits

- **DATA-4.1** `[P0][1]` 👤 The system shall assign every accepted row of a filtered version to exactly one of `train`, `val` and `test`, in configurable proportions (default 0.8 / 0.1 / 0.1), in a `split` stage that runs after `filter` and before `evaluate`. Rows that are not accepted get no split.
  - `DATA-4.1.AC1` on the COCO 10k version, every accepted row has a split and no other row has one, and each split's row count differs from its target by less than the size of one split group.
  - `DATA-4.1.AC2` proportions that are negative or do not sum to 1 are refused with `validation_error`.
- **DATA-4.2** `[P0][1]` 👤 Rows that show the same picture or the same moment shall land in the same split (leakage prevention). Accepted rows are joined into split groups when they share a duplicate group (CL-17) or their perceptual hashes are within a configurable leakage distance (default 12 bits, wider than the dedupe threshold of 8). Each group is assigned as a whole.
  - `DATA-4.2.AC1` planted near pairs (re-encoded, resized, and shots of one scene at 10 to 12 bits) always land in the same split.
  - `DATA-4.2.AC2` on the COCO 10k version, an independent all-pairs check finds no two rows within the leakage distance in different splits.
- **DATA-4.3** `[P0][1]` 👤 The split shall be stratified on row attributes, since image–caption rows have no class label. The stratum keys are configurable from: CLIP-score quartile, image orientation (landscape, portrait, square), and top-level source folder. The default uses all three.
  - `DATA-4.3.AC1` in every stratum, each split's row count differs from its target share by less than the size of one split group.
- **DATA-4.4** `[P0][1]` 👤 Splits shall be reproducible: the same parent version, configuration and seed always give the same assignments and the same version ID. The seed and the earlier split it continues (DATA-4.5) are recorded in the version's config (NFR-4).
  - `DATA-4.4.AC1` running the split twice gives identical manifests and version IDs; a different seed changes the assignments and the version ID.
- **DATA-4.5** `[P0][1]` 👤 A new version shall keep existing assignments. The split stage continues the dataset's most recent split made with the same proportions and seed:
  - rows already assigned there, matched by content hash, keep their split;
  - a row that has become the kept copy of a duplicate group inherits the split of the copy it replaced;
  - only new split groups are assigned, filling each stratum towards its target.

  The one exception is a new row that joins two existing groups from different splits. The smaller group moves to the larger one's split, and every moved row is listed in the split report.
  - `DATA-4.5.AC1` split a 9,500-row version, add 500 new images and split again: all 9,500 rows keep their split, and each split is again within one group of its target.
  - `DATA-4.5.AC2` a planted image that joins a train group and a test group moves the smaller group, and the report lists exactly the moved rows.
- **DATA-4.6** `[P0][1]` The split stage shall report the rows per split and per stratum, the number and size of split groups, the rows carried over, newly assigned and moved, and the result of the leakage check (DATA-4.2.AC2).
  - `DATA-4.6.AC1` the report's counts add up to the accepted rows and match the manifest.
- **DATA-4.7** `[P0][1]` Manual review (LABEL-1.5) shall sample from a split version and record each sampled row's split. Review decisions shall not change any row's split or status.
  - `DATA-4.7.AC1` every review item carries its row's split, and the phase-1 report (EVAL-1.1) gives the acceptance rate overall and for the test split.
- **DATA-4.8** `[P2][1]` 👤 When semantic-duplicate clusters exist (DATA-2.3), rows in one cluster shall also join one split group.
  - `DATA-4.8.AC1` two photos of one scene from different angles (far apart by pHash, close by CLIP embedding) land in the same split.

**Schema change that comes with it** (DATA_MODEL.md §5, manifest columns): add `split_group` (string: the content hash of the group's anchor, its member with the smallest `sha256`) and `split_stratum` (string, e.g. `q3|landscape|coco10k`), both set by the split stage on accepted rows. They make every assignment explainable from the manifest alone.

## How the draft answers the four questions

**1. What is stratified on, when image–caption rows have no class label?**
- **Attributes the data already has:** caption quality (CLIP-score quartile), image shape (orientation), and where the images came from (the top-level folder, the nearest thing to a class in a folder dataset).
- **Why:** each split then has the same mix of good and weak captions and of wide and tall pictures, so a score on `test` speaks for the whole dataset.
- **Measured strata:** on the 2,958 labelled COCO rows from the M4 demo and spike S-07, CLIP quartile × orientation gives 12 strata of 30 to 543 rows (square images are only 5.6 %); on the 10k set, about 100 rows or more each. A stratum of 100 rows still splits 80/10/10 to within one group, which is what DATA-4.3.AC1 checks.
- **Later, content:** a cluster of CLIP image embeddings (DATA-2.3) is the better stratum once it exists.

**2. How do near or semantic duplicates stay on one side?**
- **Duplicate groups:** the clean stage collapses them to one accepted row, so a group can never straddle a split within a version. DATA-4.5 keeps it on the same side across versions, even when a better copy takes over as the kept one.
- **Near pairs above the dedupe threshold:** the real risk is near pairs between 8 and 12 bits, which stay accepted. Measured on the same 2,958 rows (4.37 million pairs):
  - **Counts:** 2 pairs at 10 bits and 27 at 12, against 126 at 14 and 792 at 16.
  - **What they look like:** I looked at four.
    - two moments of the same snowboard jump (12 bits)
    - two planes landing on the same runway from the same spot (12 bits)
    - two different tennis photos on clay courts (10 bits)
    - a baseball field and a horse on a beach (10 bits), a false match

  pHash alone cannot tell these apart above 8 bits.
- **Why the leakage distance is wider than the dedupe one:** a wrong grouping costs only a little randomness, while a missed one lets near-copies of training pictures into `test` and flatters every score. At 12 bits, 58 rows (2 %) form 28 groups of at most 4: no chaining into large groups. At 14 bits it would be 293 rows (10 %).
- **Semantic duplicates (DATA-4.8, phase 2):** these join through the same group mechanism. One caution from the same measurement: one pair at 8 bits sat in two different versions and was never deduplicated. Duplicates are only found within a version, which is why the split works within one version and carries assignments forward rather than merging datasets.

**3. If 500 images are added later, do existing rows keep their split?**
- **The answer:** yes (DATA-4.5). The split continues the previous split's assignments and assigns only what is new.
- **Pure hashing would not do:** assigning by a hash of each row with no memory (split = hash(seed, row) mod 10) would also keep rows stable, but would hit the proportions only on average: within a stratum of 100 rows the test share would wander by several points.
- **Carry-over plus filling each stratum's shortfall** gives both exact proportions and stability. The price is that a split depends on the earlier one, which is why that version is recorded in the config (DATA-4.4).
- **The only rows that ever move:** groups that a new image joins together. That should be rare (a new image has to land within 12 bits of two existing groups at once), and the report names each move.

**4. Where does the split sit relative to the CLIP filter and the review?**
- **After the filter.** Only rows that passed it get a split, so `test` holds exactly what training would use. A new CLIP threshold reruns `filter` and then `split`, and with carry-over the rows that stay accepted keep their split.
- **The review samples the split version.** It measures label quality and does not edit the data. Its acceptance rate is reported overall and for `test`, the split whose captions later serve as evaluation targets.

## Decisions for the owner in review

1. **Proportions:** 80/10/10 gives 1,000 test images from the 10k set. Alternatively 70/15/15.
2. **Leakage distance:** 12 bits (proposed), against 10 (fewer false groupings) or 14 (catches more same-scene pairs, groups 10 % of rows).
3. **Default strata:** CLIP quartile, orientation and source folder (proposed), or fewer.
4. **The assignment method:** carry-over plus filling the shortfall (proposed: exact and stable, depends on the previous split), or pure hashing (simpler, stable with no history, proportions only on average).
5. **Review effects:** review decisions change nothing in phase 1 (proposed). The alternative is that a row rejected in review leaves `test`.
6. **Who writes the units:** the split assignment and the leakage grouping are owner's units (👤), as SPEC §9.1 places splits with the owner. The stage shell, the report and the review sampling are the agent's.
