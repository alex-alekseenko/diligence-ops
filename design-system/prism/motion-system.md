# Motion System — Prism Grid

## Animation Libraries Detected

| Library | Version | Notes |
|---|---|---|
| CSS Native | — | 7 @keyframe animations + 5 CSS transitions |
| SVG animateMotion | — | Flow particle dots along Sankey link paths |

No third-party animation libraries (GSAP, Framer Motion, etc.) used. Prism relies entirely on CSS @keyframes, CSS transitions, and SVG `<animateMotion>` for ambient particle effects.

## Duration Scale

| Token | Value | Extracted From | Use Case |
|---|---|---|---|
| `--duration-instant` | 50ms | — | Imperceptible state changes |
| `--duration-faster` | 150ms | tooltip opacity (0.15s) | Tooltip fade, dot hover |
| `--duration-fast` | 250ms | sankey node transition (0.25s) | Node/link hover effects |
| `--duration-normal` | 300ms | popIn, link opacity (0.3s) | Interaction feedback |
| `--duration-moderate` | 500ms | sankeyFadeIn (0.5s) | Sankey diagram entrance |
| `--duration-slow` | 700ms | slideIn (0.7s) | Section entrance animations |
| `--duration-slower` | 800ms | stackGrow (0.8s) | Stacked bar segments |
| `--duration-extra-slow` | 2500ms | strokeDraw (2.5s) | SVG trend line draw |

**Key finding:** Prism uses notably slow animation durations compared to typical SaaS (700-800ms for entrances vs. typical 200-400ms). This creates an editorial, cinematic reveal — the dashboard tells a story as it loads. The spring easing `cubic-bezier(0.22, 1, 0.36, 1)` makes these long durations feel responsive by front-loading the motion.

## Easing Curves

| Token | Value | Classification | Use Case |
|---|---|---|---|
| `--ease-out` | `ease-out` | Standard deceleration | DEFAULT — section entrances, fades, dot pop-in |
| `--ease-in-out` | `ease-in-out` | Symmetric | Scroll hint pulse animation |
| `--ease-spring` | `cubic-bezier(0.22, 1, 0.36, 1)` | Aggressive spring | Data bar growth, stack segment growth — the signature Prism curve |
| `--ease-smooth` | `cubic-bezier(0.45, 0.05, 0.55, 0.95)` | Gentle symmetric | Smooth transitions |
| `--ease-linear` | `linear` | — | SVG flow dot `animateMotion` only |

**Key finding:** Prism's signature easing is `cubic-bezier(0.22, 1, 0.36, 1)` — an aggressive spring with fast acceleration and gentle landing. Used for all data visualization animations (bars, stacks). This is similar to the "expo-out" family of curves, giving bars a snappy, energetic feel that contrasts with the otherwise editorial restraint of the design.

## Hover Effects

### Tier 1: Subtle (opacity/color only)

| Element | Properties Changed | From → To | Duration | Easing |
|---|---|---|---|---|
| Sankey link paths | fill-opacity, stroke-opacity | default → dimmed(0.03) / highlighted(0.3) | 300ms | ease |
| Period pills | background, color, border-color | transparent → ink bg | 200ms | ease |
| Trend dots (SVG) | r attribute | 4.5 → 7 | 150ms | ease |

### Tier 2: Moderate (stroke/border emphasis)

| Element | Properties Changed | From → To | Duration | Easing |
|---|---|---|---|---|
| Sankey node rects | stroke-width, stroke-color | 1px ghost → 2px muted | 250ms | ease |
| Sankey node (focus) | stroke-width, stroke-color | → 2.5px revenue blue | 250ms | ease |

### Tier 3: Expressive (multi-property)

| Element | Properties Changed | From → To | Duration | Easing |
|---|---|---|---|---|
| None detected | — | — | — | — |

**Finding:** Prism's hover effects are minimal and data-focused — no dramatic transforms, no shadow lifts, no scale changes on cards. Hover interactions serve one purpose: highlighting data relationships in the Sankey diagram. This is characteristic of editorial/infographic design where the data is the hero.

## Keyframe Animations

| Name | Duration | Easing | Delay Pattern | Used On | Purpose |
|---|---|---|---|---|---|
| `slideIn` | 700ms | ease-out | 0s, .1s, .2s, .3s, .4s stagger | Header, ticker, sections, footer | Page entrance — sections reveal top-to-bottom |
| `barSlide` | 600ms | spring | .3s + i×.04s per bar | Revenue/expense bars | Bars grow upward from zero height |
| `strokeDraw` | 2500ms | ease-out | 500ms | Trend line SVG path | Line draws itself across chart via stroke-dashoffset |
| `fadeIn` | 1200ms | ease-out | 800ms+ | Trend area fill, annotations | Slow fade after stroke draw completes |
| `popIn` | 300ms | ease-out | 1.4s + i×.08s per dot | Trend data points | Dots appear sequentially along trend line |
| `stackGrow` | 800ms | spring | .8s–1.9s per segment | Quarter breakdown segments | Segments grow from 0% flex-basis |
| `sankeyFadeIn` | 500ms | ease-out | col×.15s stagger | All Sankey elements | Column-by-column entrance, left to right |
| `hintPulse` | 2000ms | ease-in-out | 1s, repeats 3× | Mobile scroll hint | Subtle horizontal pulse to indicate scrollability |

### Stagger Patterns

Prism uses two distinct stagger strategies:

1. **Section stagger**: Each dashboard section has a fixed delay increment (.1s between sections). Creates a "reading order" reveal from top to bottom.

2. **Column stagger**: Sankey diagram elements are staggered by column index (`col × 0.15s`). Creates a left-to-right data flow reveal matching the Sankey's reading direction.

3. **Per-element stagger**: Bar chart bars and trend dots use per-element micro-delays (0.04s per bar, 0.08s per dot). Creates a wave/cascade effect within each chart.

## SVG Animations

### Flow Dots (animateMotion)

The Sankey diagram uses SVG `<animateMotion>` with `<mpath>` references to move small particle rectangles along link paths:

- **Shape**: 2.5×2.5px rect, rounded 0.5px
- **Color**: matches target node color
- **Opacity**: 0.2 (very subtle)
- **Duration**: 3-6s (randomized per dot)
- **Delay**: staggered with randomization
- **Count**: max 2 per link, proportional to flow value
- **Repeat**: infinite

This creates ambient, flowing particle motion that reinforces the "flow" metaphor of the Sankey diagram without being distracting.

## Scroll-Triggered Animations

None detected. All animations fire on page load with fixed delays.

## Component Motion Patterns

### Dashboard Sections
- **Entrance**: `slideIn` 700ms ease-out with staggered delays (0–.4s)
- **Pattern**: Top-to-bottom reveal mimicking reading order

### Bar Chart
- **Bar growth**: `barSlide` 600ms spring from zero height
- **Trend line**: `strokeDraw` 2500ms ease-out — slow, cinematic line draw
- **Data dots**: `popIn` 300ms ease-out — sequential pop after line completes
- **Annotations**: `fadeIn` 1200ms ease-out — appears last in sequence

### Sankey Diagram
- **Node entrance**: `sankeyFadeIn` 500ms ease-out, column-staggered
- **Link entrance**: same as nodes but delayed by source column
- **Node hover**: stroke emphasis (Tier 2)
- **Link hover**: opacity dimming system — non-connected links dim to 0.03 opacity
- **Flow dots**: ambient SVG particle animation (infinite)
- **Tooltip**: 150ms opacity fade, positioned with boundary detection + caret flip

### Stacked Bars
- **Segment growth**: `stackGrow` 800ms spring from 0% flex-basis
- **Labels**: percentage labels ride inside segments

### Metric Tiles
- **Entrance**: inherits parent section slideIn
- **No individual animation**

### Tooltips
- **Show**: opacity 0→1, 150ms ease
- **Hide**: opacity 1→0, 150ms ease
- **Position**: absolute, follows mouse with boundary clamping
- **Caret**: CSS ::after triangle, flips on boundary detection
- **Dark theme**: ink background (#1a1814), canvas text

## Cross-References

- **Shadow colors**: Use warm-tinted `oklch(0.172 0.012 75 / %)` matching `--foreground` hue ~75° (warm brown)
- **Focus ring**: Uses `--ring` = `--revenue` blue `oklch(0.623 0.140 255)`
- **Data colors**: Motion highlights use semantic color tokens (--revenue, --profit, --cost)
- **Flow dots**: colored by target node's semantic type color

## Tailwind Class Recipes

```
/* Section Entrance (Prism slideIn style) */
className="animate-[slideIn_700ms_ease-out_both]"

/* Bar Chart Bar */
className="animate-[barSlide_600ms_cubic-bezier(0.22,1,0.36,1)_both] origin-bottom"

/* Sankey Element Entrance */
className="animate-[sankeyFadeIn_500ms_ease-out_forwards]"

/* Period Pill (active state) */
className="transition-all duration-200 bg-foreground text-background"

/* Tooltip */
className="transition-opacity duration-150 ease-out"

/* Stacked Bar Segment */
className="animate-[stackGrow_800ms_cubic-bezier(0.22,1,0.36,1)_both]"
```

## Accessibility

- `prefers-reduced-motion` handling: included in motion-system.css — disables all animations and transitions
- Flow dots: explicitly hidden with `display: none !important` under reduced-motion
- Bar/trend animations: forced to final state with `opacity: 1`, `transform: none`, `stroke-dashoffset: 0`
- Sankey: all elements set to `opacity: 1` with no animation
- Focus indicators: Sankey nodes have `tabindex="0"` + `role="button"` + `aria-label` for keyboard navigation
- Keyboard: focus triggers same highlight behavior as hover

## DO NOT

- Hardcode duration values — use `var(--duration-*)` tokens
- Use layout-triggering animations (width, height, margin, padding)
- Exceed 800ms for interactive hover animations (the 700-2500ms durations are for entrance-only)
- Use bounce/elastic easing — Prism's spring curve (`0.22, 1, 0.36, 1`) is the maximum expressiveness
- Add hover scale effects to data visualizations — Prism intentionally avoids transform-based hovers
- Make flow dots larger than 3px or more opaque than 0.3 — they must remain ambient
- Skip `prefers-reduced-motion` — the source explicitly supports it
- Add infinite animations beyond the flow dots — Prism is otherwise static after entrance
- Use the `--duration-extra-slow` token for anything other than decorative SVG line draws
