# Bundled fonts

The two typefaces of the design system ([docs/DESIGN-SYSTEM.md](../../docs/DESIGN-SYSTEM.md), typography). They are kept in the repository so nothing is fetched from the network at run time (NFR-7). Downloaded on 2026-11-15 with the owner's approval, from each project's official release tag on GitHub.

| File | Weight | Source (release tag) | Bytes | SHA-256 (first 16) |
|---|---|---|---|---|
| `IBMPlexSans-Regular.woff2` | 400 | [IBM/plex](https://github.com/IBM/plex) `@ibm/plex-sans@1.1.0`, `packages/plex-sans/fonts/complete/woff2/` | 63,020 | `BA711A3085FF9F27` |
| `IBMPlexSans-Medium.woff2` | 500 | same | 66,740 | `5660F8A658F8BB50` |
| `IBMPlexSans-SemiBold.woff2` | 600 | same | 67,060 | `F78048030EAB62E8` |
| `IBMPlexSans-LICENSE.txt` | – | same tag, `packages/plex-sans/LICENSE.txt` | 4,456 | `7E6B2818EDBD8F6A` |
| `JetBrainsMono-Regular.woff2` | 400 | [JetBrains/JetBrainsMono](https://github.com/JetBrains/JetBrainsMono) `v2.304`, `fonts/webfonts/` | 92,164 | `A9CB1CD82332B23A` |
| `JetBrainsMono-Medium.woff2` | 500 | same | 93,824 | `086C48DFBEA9DDAF` |
| `JetBrainsMono-OFL.txt` | – | same tag, `OFL.txt` | 4,399 | `30F0C136E3C88E42` |

**Licence.** Both families are under the SIL Open Font License 1.1 (the two `.txt` files). It allows bundling and redistribution with software; the fonts may not be sold on their own, and a modified version may not use the reserved names. Keep the licence files next to the fonts.

**Only the weights the design system uses** are here: Plex Sans 400, 500 and 600; JetBrains Mono 400 and 500. Add others from the same tags if a later design version needs them.
