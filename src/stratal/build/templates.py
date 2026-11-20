"""Built-in model templates (BUILD-1.7, ADR-0017 rule 9).

Each is a graph spec for one dataset type. The MLP and the small CNN are the
M8 reference models written as graphs, with the same layers and parameter
counts; a run started from them is a new lineage, because a graph is a new
spec (ADR-0017 rule 2).
"""

from dataclasses import dataclass

from stratal.build import layers as L
from stratal.build.spec import GraphSpec, Node, chain


@dataclass(frozen=True)
class Builtin:
    name: str
    description: str
    dataset_type: str
    spec: GraphSpec


def _cnn_block(width: int) -> list[object]:
    return [
        L.Conv2d(out_channels=width, bias=False),
        L.BatchNorm(),
        L.ReLU(),
        L.Conv2d(out_channels=width, bias=False),
        L.BatchNorm(),
        L.ReLU(),
        L.MaxPool2d(),
    ]


def _residual_cnn() -> GraphSpec:
    nodes: dict[str, object] = {
        "stem": L.Conv2d(out_channels=32, bias=False),
        "stem_bn": L.BatchNorm(),
        "stem_relu": L.ReLU(),
    }
    edges = [("input", "stem"), ("stem", "stem_bn"), ("stem_bn", "stem_relu")]
    previous = "stem_relu"
    for block in (1, 2):
        conv1, bn1, relu1 = f"b{block}_conv1", f"b{block}_bn1", f"b{block}_relu1"
        conv2, bn2, add, relu2 = f"b{block}_conv2", f"b{block}_bn2", f"b{block}_add", f"b{block}_out"
        nodes |= {
            conv1: L.Conv2d(out_channels=32, bias=False),
            bn1: L.BatchNorm(),
            relu1: L.ReLU(),
            conv2: L.Conv2d(out_channels=32, bias=False),
            bn2: L.BatchNorm(),
            add: L.Add(),
            relu2: L.ReLU(),
        }
        edges += [
            (previous, conv1),
            (conv1, bn1),
            (bn1, relu1),
            (relu1, conv2),
            (conv2, bn2),
            (bn2, add),
            (previous, add),  # the skip connection (BUILD-1.9)
            (add, relu2),
        ]
        previous = relu2
    nodes["pool"] = L.GlobalAvgPool()
    edges += [(previous, "pool"), ("pool", "output")]
    return GraphSpec(
        label="residual_cnn",
        nodes=[Node.model_validate({"id": k, "layer": v}) for k, v in nodes.items()],
        edges=edges,
    )


BUILTINS: tuple[Builtin, ...] = (
    Builtin(
        "MLP",
        "Two hidden layers (256, 128) on the flattened image: M8's reference MLP.",
        "labelled_images",
        chain(
            "mlp",
            L.Flatten(),
            L.Linear(out_features=256),
            L.ReLU(),
            L.Dropout(p=0.2),
            L.Linear(out_features=128),
            L.ReLU(),
        ),
    ),
    Builtin(
        "Small CNN",
        "Three blocks of two 3×3 convolutions with batch norm, then a global average pool: "
        "M8's reference CNN.",
        "labelled_images",
        chain("small_cnn", *_cnn_block(32), *_cnn_block(64), *_cnn_block(128), L.GlobalAvgPool()),
    ),
    Builtin(
        "Residual CNN",
        "A stem and two residual blocks, each adding its input back (BUILD-1.9).",
        "labelled_images",
        _residual_cnn(),
    ),
    Builtin(
        "LSTM text classifier",
        "Token embeddings read by an LSTM; its final state is classified.",
        "labelled_text",
        chain("lstm_text", L.Embedding(embedding_dim=64, padding_idx=0), L.LSTM(hidden_size=128)),
    ),
    Builtin(
        "Transformer text classifier",
        "Embeddings with positions, one self-attention block, then the mean over positions.",
        "labelled_text",
        chain(
            "transformer_text",
            L.Embedding(embedding_dim=64, padding_idx=0),
            L.PositionalEncoding(),
            L.Attention(num_heads=4),
            L.LayerNorm(),
            L.GlobalAvgPool(),
        ),
    ),
    Builtin(
        "Tabular MLP",
        "Two hidden layers on the encoded columns (the tabular input block).",
        "tabular",
        chain(
            "tabular_mlp",
            L.Linear(out_features=64),
            L.ReLU(),
            L.Dropout(p=0.1),
            L.Linear(out_features=32),
            L.ReLU(),
        ),
    ),
)
