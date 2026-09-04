# Vendored: Slope

Upstream: <https://github.com/pascal-schetelat/Slope> (MIT, see `LICENSE`)
Vendored from commit `e706d6fc40b609f86a3d034e00a8d67cd384fb0e` (2015-11-12).

Used by `Weekly_Analysis.ipynb` for the genre bump plot, via
`from plotSlope import slope`.

Upstream is unmaintained and does not run on current Python/matplotlib, so
`plotSlope.py` carries two local patches:

1. `nt = int(fh // (font.get_size() / 72) / 2)` — Python 3 requires an int
   where a float was previously accepted.
2. `Axis.get_text_widths()` was removed from matplotlib; the y tick-label
   width is now measured directly with `get_window_extent()`.

Verified against matplotlib 3.10.9. If a future matplotlib breaks the bump
plot again, this file is the place to look — the notebook logs the exception
and carries on without the plot rather than failing the whole report.
