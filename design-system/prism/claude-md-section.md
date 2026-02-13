## Design System

Reference: `design-systems/prism/design-system.md`
Tokens: `design-systems/prism/extracted-globals.css`
Motion: `design-systems/prism/motion-system.css`

### Quick Reference

- **Primary**: `bg-primary` = revenue blue `oklch(0.623 0.140 255)` / #4a90d9
- **Foreground**: `text-foreground` = warm near-black `oklch(0.172 0.012 75)` / #1a1814
- **Background**: `bg-background` = warm parchment `oklch(0.980 0.008 80)` / #faf8f4
- **Muted text**: `text-muted-foreground` = warm gray `oklch(0.604 0.018 73)` / #8a8278
- **Border**: `border-border` = warm light gray `oklch(0.806 0.013 76)` / #c4bfb6
- **Success/Profit**: `--profit` = green `oklch(0.635 0.144 160)` / #3aaa6d
- **Destructive/Cost**: `--cost` = red `oklch(0.590 0.170 25)` / #d95a5a
- **Warning/Ochre**: `--ochre` = gold `oklch(0.700 0.140 80)` / #c49a3c
- **Display font**: Fraunces (serif) — weight 600-700 for headings, 400 for italic accents
- **Body font**: IBM Plex Mono (monospace) — weight 400-500 for all UI text
- **Radius**: `--radius: 0.125rem` (2px base — near-square)

### Color Rules

- NEVER hardcode colors — use semantic tokens (`bg-primary`, `text-muted-foreground`)
- NEVER use Tailwind palette colors (`bg-blue-500`, `text-gray-700`)
- All neutrals are **warm-tinted** (brown/amber hue ~75°), NOT cool/blue-tinted
- Data colors are semantic: blue=revenue, green=profit, red=cost, gold=accent

### Typography Rules

- Display/headings: Fraunces serif only (never for body text)
- All body/UI text: IBM Plex Mono monospace (never sans-serif)
- Labels: uppercase, letter-spacing 1.5-3px, 10-11px
- Italic Fraunces (weight 400) for inline subtitle annotations

### Spacing Rules

- Base unit: 4px grid
- Major sections: 56px separation
- Between sections: 40px
- Inside panels: 32px padding
- Max-width: 1160px centered

### Animation Rules

- Duration: use `var(--duration-*)` tokens (slow=700ms for entrances, fast=250ms for hovers)
- Easing: `cubic-bezier(0.22, 1, 0.36, 1)` (spring) for data viz, `ease-out` for content
- Stagger: section-level delays for reading-order reveal
- ALWAYS support `prefers-reduced-motion`
- NEVER exceed 800ms for interactive hover animations

### Critical Constraints

- Do not use cool/blue-tinted grays — Prism uses warm brown-tinted neutrals
- Do not use sans-serif fonts — only serif (Fraunces) + monospace (IBM Plex Mono)
- Do not use box-shadow on panels/cards — Prism is flat with borders only
- Do not use border-radius > 2px — the editorial aesthetic demands near-square
- Do not add hover shadow/lift effects — panels are static, hovers are data-interaction only
- Do not use bounce/elastic easing — spring curve is the max expressiveness
