"""Write scikit-learn's bundled tabular datasets as CSV sources (M10, decision 1, ADR-0019 rule 7).

Nothing is downloaded: the three datasets ship inside the scikit-learn package,
so this runs in the ``worker-cpu`` image, which has it, with ``sources`` mounted
writable:

    docker compose run --rm --no-deps -v ./scripts:/app/scripts:ro -v ./sources:/app/sources \
        worker-cpu python -I scripts/datasets/sklearn_tables.py

It writes one folder per table under ``sources/`` (one table per source, as
tabular ingest expects), with the target as its last column:

- ``breast-cancer/breast_cancer.csv``: 569 rows, 30 numeric features, target
  ``diagnosis`` (``malignant`` or ``benign``): classification;
- ``diabetes/diabetes.csv``: 442 rows, 10 numeric features (as scikit-learn
  scales them), target ``progression`` (a number): regression;
- ``wine/wine.csv``: 178 rows, 13 numeric features, target ``cultivar``
  (``class_0`` to ``class_2``): classification.

Ingest them with ``dataset_type: tabular`` and ``stages.ingest.table.target``
set to the target's name. Nothing here runs inside Stratal itself.
"""

# pyright: basic, reportMissingImports=false

import argparse
import csv
from pathlib import Path
from typing import Any

from sklearn.datasets import load_breast_cancer, load_diabetes, load_wine

TABLES: dict[str, tuple[Any, str, bool]] = {
    "breast-cancer": (load_breast_cancer, "diagnosis", True),
    "diabetes": (load_diabetes, "progression", False),
    "wine": (load_wine, "cultivar", True),
}


def column_name(name: str) -> str:
    """Feature names as plain column names: 'mean radius' → 'mean_radius'."""
    return "_".join(name.replace("(", " ").replace(")", " ").split()).lower()


def write(out: Path, folder: str) -> Path:
    loader, target, classes = TABLES[folder]
    data = loader()
    names = [column_name(str(n)) for n in data.feature_names]
    path = out / folder / f"{folder.replace('-', '_')}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([*names, target])
        for features, goal in zip(data.data, data.target, strict=True):
            value = str(data.target_names[int(goal)]) if classes else repr(float(goal))
            writer.writerow([*(repr(float(v)) for v in features), value])
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("sources"))
    args = parser.parse_args()
    for folder in TABLES:
        path = write(args.out, folder)
        rows = sum(1 for _ in path.open(encoding="utf-8")) - 1
        print(f"{path}: {rows} rows")


if __name__ == "__main__":
    main()
