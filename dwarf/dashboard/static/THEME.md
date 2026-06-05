# Dashboard theme contract

The Dwarf dashboard ships with a default theme (`forensic-noir` —
brutalist obsidian + cyan-glow) and supports user-supplied alternates
via CSS custom-property overrides scoped under `[data-theme="<slug>"]`
on the `<html>` element.

## Selecting a theme

The active theme is resolved at render time, highest priority first:

1. `ADA2_DWARF_DASHBOARD_THEME` environment variable (operator override).
2. `dashboard_theme: <slug>` field in `dwarf/state/config.yaml`.
3. `forensic-noir` (built-in default).

Unknown slugs degrade silently to the default.

## Built-in themes

| slug                | description                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------- |
| `forensic-noir`     | Default. Deep obsidian field, cyan-glow accents, single crimson alert state.                 |
| `light-audit`       | Paper-white field, ink-black text, deep-teal accents, crimson alert. Printable audit report. |
| `monochrome-print`  | Pure greyscale. No accent color. Suitable for greyscale printing / accessibility audits.    |

## Adding a new theme

1. Add the slug to `SUPPORTED_THEMES` in `dwarf/profile_manager/data/dashboard_theme.py`.
2. Append a `[data-theme="<slug>"] { ... }` block to
   `dwarf/dashboard/static/css/themes.css` overriding the tokens listed
   below. Tokens you don't override fall back to `forensic-noir`.

## Token contract

Any custom property defined in `tokens.css` under `:root` MAY be
overridden by a theme. The load-bearing surface is documented here;
omitted tokens still resolve to their default values.

### Surface tokens (pages, cards, raised UI)

```
--obsidian-0   /* page background */
--obsidian-1   /* content well */
--obsidian-2   /* card / panel surface */
--obsidian-3   /* elevated / hover surface */
--obsidian-4   /* most-elevated surface (modals, popovers) */
--paper        /* alias of --obsidian-1 */
--paper-raised /* alias of --obsidian-2 */
```

### Accent tokens (load-bearing brand)

```
--cyan-primary /* primary accent — links, active pills, focus rings */
--cyan-glow    /* hover / focus state for accents */
--cyan-deep    /* depressed / pressed state */
--cyan-trace   /* subtle accent border / outline */
--cyan-tint    /* very subtle accent fill */
--accent       /* alias of --cyan-primary */
--accent-deep  /* alias of --cyan-deep */
--accent-tint  /* alias of --cyan-tint */
```

### Alert tokens

```
--crimson      /* error / fail state */
--crimson-glow /* error highlight */
--crimson-deep /* depressed error */
```

### Ink tokens

```
--ink-primary   /* body text */
--ink-secondary /* metadata / sub-text */
--ink-tertiary  /* eyebrow labels / dim chrome */
```

### Atmosphere tokens

```
--hud-trace-color /* grid lines on the substrate-breathing background */
```

## Sample override

```css
[data-theme="my-theme"] {
  --obsidian-0: #0a0a12;
  --cyan-primary: #ff6b35;
  --cyan-glow: #ffaa66;
  --crimson: #00d18c; /* re-purpose alert as success */
}
```

Drop that into `themes.css`, register the slug, set
`dashboard_theme: my-theme` in `state/config.yaml`, restart the
dashboard. The change applies to every page automatically because
all components read from the variables.
