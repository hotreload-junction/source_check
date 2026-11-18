# Graph view (task F3a)

How the result graph shows article -> claims -> sources without turning into a hairball.
Data: `result.graph`, `result.claims`, `result.sources`, `result.domains` (contracts/api.md).

## Core idea

The verdict belongs to the **claim-source pair**, not to the claim or the source alone: one source can
support claim 1 and be distorted by claim 3. So:

- Claims are their own column. Do **not** merge all claims into one tree (the per-pair verdict is lost).
- Do **not** draw one separate tree per claim as the main view (shared sources get duplicated and the
  "same operator across claims" story disappears).

## Layout

```
article  ->  claims (<= 6)  ->  sources (depth 1)  ->  sources of sources (depth 2, collapsed)
```

- Cytoscape, layout `dagre` (or `elk` layered), `rankDir: 'LR'`. Rank by `depth` / node type.
- Each source node appears **once**. Several claims citing it = several `cites` edges into one node;
  show "cited 2x" in its subtitle.
- **Edge colour = claim verdict for that pair:** supported = green, distorted = amber,
  contradicted / not_mentioned / unsourced = red, not_checkable = grey. Nodes stay neutral; claim nodes
  get a small verdict dot.
- `unsourced` claim: short dashed red stub labelled "no source" instead of an empty node.
- Not-checkable sources (social, paywall, dead): small grey dot, label on hover. They are neutral and
  must look neutral.
- Operator clusters: domains with the same `cluster_id` become a compound parent node (dashed box,
  label like "same operator · G-XXXX"). `shares_infra` edges can then be hidden; the box says it.
- Depth 2 collapsed by default; click a source to expand its sources. `cites_back` edges (citation
  circle) drawn thicker, always visible.

## Interaction

- Click a claim (graph or article text) -> add class `faded` (opacity ~0.15) to every element not on
  that claim's path; click again or Esc -> show all.
- Click a `cites` edge -> evidence panel: verdict, verbatim quote (original + translation), fuzzy score,
  one-sentence explanation, link to the source.
- The existing per-claim tree component (screenshot from Sat) is the detail panel, not the main view.

## No made-up numbers

No 0-100 scores on nodes or weights on edges: the pipeline produces categorical verdicts, and Hostinger
judges the honesty of the detection logic. The only real per-edge number is `evidence.fuzzy_score`
(quote match); show it in the evidence panel, labelled as such.

## Budget

With 6 claims and 10-15 sources this stays readable. If a demo article has more than ~20 sources,
group not-checkable sources into one grey "+N not checkable" node.

## Narrow screens (phones, browser-extension side panel)

Up to 640 px wide (`NARROW_Q` in `frontend/src/responsive.ts`) the same tree is drawn as a vertical outline
(`frontend/src/vlayout.ts`): document on top, claims stacked below, each claim's sources indented under it, joined by
a neutral trunk and one elbow per edge (verdict colour, thick = evidence). The verdict pill sits on the top edge of the
card it leads to; a shared source or citation circle curves round the right-hand side. The graph is fitted to the
width (~95 % zoom instead of ~20 % for the wide tree) and scrolls vertically; scrolling stops at the graph's edges.
The canvas fills the screen height under the header and the trust bar. Same cards, colours, verdicts and detail
sheets as on desktop; 641-900 px (tablets) keeps the wide tree.
