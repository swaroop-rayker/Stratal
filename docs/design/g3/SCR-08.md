# SCR-08 model builder — Gate 3 comparison

| | |
|---|---|
| Mockup | [`screens/SCR-08_v1.html`](../screens/SCR-08_v1.html), G2 approved 2026-11-19 |
| Built | `frontend/src/screens/build/` (`BuilderScreen`, `GraphCanvas`, `parts`, `layers`), `frontend/src/lib/graph.ts`, `frontend/src/api/builder.ts`, `frontend/src/styles/scr08.css`; route `/models/build` (`?version=` the split, `?view=list` the keyboard view) |
| Checked | 2026-11-19, live at 1280 × 760 (contrast, the demo), 1400 × 900 (frames A and E) and at the pane's 800 px (the narrow layout), dark and light, on the CIFAR-10 split `ds_f8e67211dd94`: the Small CNN template, then a CNN built layer by layer from the palette (`my_model`, 10,602 parameters), trained as `job_0053` |
| Status | Waiting for the owner's review |

## Screenshots (in `SCR-08/`)

| File | Mockup frame | What it shows |
|---|---|---|
| `live-E-start-templates-dark.jpg` | E | The start screen: the three built-in image templates with their parameter counts on this dataset's ends (MLP 820,874; Residual CNN 38,378; Small CNN 288,746) |
| `live-A-small-cnn-dark.jpg` | A (at 1400 px) | The Small CNN template loaded: palette with the sequence layers greyed, the canvas fitted to 22 layers, "288,746 parameters", memory split for Adam, "No problems", cnn |
| `live-G-narrow-canvas-dark.jpg` | G | The narrow layout: the *Layers* menu in the graph's header, one column, the summary bar below |
| `live-G-narrow-inspector-drawer-light.jpg` | G | The narrow layout in light: the selected layer's settings in a drawer |

The light theme was checked live at 1280 px as well (contrast below). Frame C (problems) and frame D (list view) are covered by their Vitest tests rather than screenshots; frame F's error state is the shared `ErrorState`.

## Differences from the mockup

1. **The dataset is chosen with two selects**, the dataset and then its split version, instead of one combined select; only split versions are listed.
2. **Palette tiles add on click**, after the selected layer (or before the output); dragging a tile onto the canvas is not built. Connecting two nodes by dragging from a handle is. A tile's name is "Add Conv2d after the selected layer".
3. **The canvas controls are React Flow's own** (zoom in, zoom out, fit) in the bottom-left corner; the mockup draws two buttons. The view fits the graph when it opens; it does not refit after each added layer.
4. **The narrow layout starts below 1280 px**, not at 1024: under the design system's desktop breakpoint the palette becomes the *Layers* menu (offering only the layers the dataset takes) and the inspector a drawer.
5. **The inspector shows the output shape and the parameter count**, not the input shape: the input is the previous node's output, shown on its card. A merge node's inputs are listed in the inspector and edited in the list view.
6. **The graph's label changes only through *Save as template*** (the template's name becomes the label); there is no separate rename.
7. ***Continue to training* is also disabled for a dataset type that cannot be trained yet** (tabular, text: M10), with the reason as its tooltip.
8. **Saved templates on the start screen** have *Delete* beside *Use*; built-in ones do not.

## Accessibility

- **Contrast:** every text element passes AA, scanned after the theme transition had settled: 113 (dark) and 112 (light) with a 10-layer CNN, the palette, the inspector and the summary bar. Greyed palette tiles are disabled controls and exempt.
- **Keyboard:** the list view is the full keyboard path: each layer row is a tab stop, Enter selects it (the inspector shows it), Delete removes it, ↑ ↓ move it within a chain, and each row's *Inputs* select edits its edges (several for a merge). <kbd>a</kbd> moves focus to the first palette tile. Greyed tiles are not focusable.
- **Names:** each node card has a text name ("c1, Conv2d, output image 32 by 32 by 32, 896 parameters"; with a problem, its sentence). The summary bar is a polite status region, so an edit announces "No problems" or the count.

## Found while testing

- **The start screen's template list reloaded under the pointer.** It first loaded without a dataset type, then again with it, replacing the *Use* buttons just as one was clicked. The start screen now waits for the dataset's ends (a skeleton meanwhile). Caught by `test_BUILD_1_7_a_template_starts_the_graph`.
- **"Add a first layer" added a Linear layer to an image dataset** (an immediate problem). It now adds the first layer that fits the input: a convolution for images, an embedding for tokens, a linear layer for a table.
- **The builder's report model collided with the evaluation report's name in OpenAPI**, renaming both in the generated types and breaking SCR-06's; the builder's is now `SpecReport`.

## Tests

`frontend/src/screens/build/Builder.test.tsx`: `test_UI_2_3_a_field_added_to_a_layer_schema_appears_in_its_form_with_no_ui_change`, `test_DATA_5_5_the_palette_offers_only_layers_the_dataset_type_takes`, `test_BUILD_1_3_changing_the_dataset_updates_the_locked_input_and_output`, `test_BUILD_1_4_problems_are_listed_on_their_nodes_and_block_continuing`, `test_BUILD_1_7_a_template_starts_the_graph`; `frontend/src/lib/graph.test.ts` (eight tests of the graph edits and layout, including `test_BUILD_1_9_*`).
