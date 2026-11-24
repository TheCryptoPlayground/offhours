# Site design notes

Subject: a research tracker for tokenized US stocks traded outside US market hours.
Audience: market-structure researchers, regulators, crypto analysts. Primary job: show when
these tokens trade relative to the US session clock, how that compares with the stocks
themselves, and whether weekend token prices anticipate Monday's open.

## Tokens

Colour encodes the session clock, and one colour carries the argument.

| Name        | Light     | Dark      | Use                                          |
|-------------|-----------|-----------|----------------------------------------------|
| Fog         | `#EEF1F4` | `#17212B` | page                                         |
| Slate ink   | `#15212B` | `#E4EAF0` | text                                         |
| Exchange    | `#2F5F8F` | `#5C8FC4` | regular hours                                |
| Pre / After | `#7FA6C6` / `#8F82C2` | lighter | extended hours                  |
| Night       | `#4C3D8C` | `#7A6CC4` | overnight (the stock trades somewhere)       |
| Lamp amber  | `#D9861C` | `#F0A23A` | closed: no US venue open, only tokens trade  |

Blues and violets mean the stock itself can trade somewhere; amber means only tokens can.
Amber is the one bold element; everything else stays quiet.

Type: Schibsted Grotesk (self-hosted, OFL), one family. Scale 14 / 16 / 18 / 21 / 48 px,
tabular figures in tables.

## Layout

Single left-aligned column, prose capped near 70 characters, charts allowed wider.

```
Tokenized Stock Observatory
How much tokenized-stock trading happens while US markets are shut ...
[ week grid: Mon..Sun x 00:00..23:30 ET, colour = session, depth = volume ]
[ legend doubles as the session definitions ]
Across the full archive: $X traded, Y% outside regular hours, Z% while every US venue was shut ...

Daily volume by session            [Dollars | Share]      bars, 23/5 start marked
Tokens and their stocks keep different hours   100% bars + session table
Where the volume is                [Venue | Issuer]
By stock                           sortable table, row opens a per-stock chart
Do weekend token prices anticipate the next open?   scatter + plain-language stats
Method and coverage                sessions, sources and status, caveats, downloads
```

## Review against defaults (what changed)

- First draft opened with a big-number KPI strip. Replaced by the week grid: the session
  clock is the most characteristic thing in this subject, and it shows the finding at once.
- KPI cards became one sentence with numbers in it, so figures carry their context.
- Avoided card chrome: sections are separated by space and a single rule, not boxed tiles.
- No uppercase eyebrows, no middle-dot meta strings, no monospace labels, no arrow links.
