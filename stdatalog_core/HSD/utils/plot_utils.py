"""Plotly helpers to draw labeled regions/tags and color utilities.

Functions assume Plotly figures and pandas DataFrames with a 'Time' column and
flag columns (e.g., label==1.0) to generate vertical regions.
"""

# ******************************************************************************
# * @attention
# *
# * Copyright (c) 2022 STMicroelectronics.
# * All rights reserved.
# *
# * This software is licensed under terms that can be found in the LICENSE file
# * in the root directory of this software component.
# * If no LICENSE file comes with this software, it is provided AS-IS.
# *
# *
# ******************************************************************************
#
# Module purpose:
#   - Utilities to draw regions/tags on Plotly figures based on labeled or flagged data.
#   - Provide a consistent color palette and helpers to manipulate colors.
#
# Design notes:
#   - Functions assume Plotly Figure objects (graph_objects or express figures).
#   - Data inputs (ss_data_frame) are expected to be pandas DataFrames with specific columns:
#       * 'Time' column with numeric or datetime values used for x0/x1 placement
#       * A flag column whose name is provided via 'label' and contains 1.0 for active regions
#
# Plotly specifics:
#   - Vertical rectangles (add_vrect) are used to visualize regions across Y.
#   - Legend entries are created with "dummy" scatter traces to display region labels.
#   - Subplot targeting is supported via xref/yref mapping when row/col are provided.
#
# Error handling:
#   - Functions assume well-formed inputs; minimal validation is performed.
#   - If columns are missing or types are incorrect, Plotly/pandas will raise.

import numpy as np


class PlotUtils:
    """
    Collection of static plotting utilities for Plotly figures.

    Provides:
        - Color palette for lines.
        - Helpers to draw rectangular regions based on tags or flag columns.
        - Utility to darken hex colors by a percentage.

    Usage:
        - PlotUtils.draw_tags_regions(fig, label_time_tags)
        - PlotUtils.draw_regions(fig, df, 'flag_col', 'rgba(255,0,0,0.5)')
        - PlotUtils.darken_color('#ff0000', 20)
    """

    # A list of colors to be used for line plotting, for example in a graph.
    # Hex strings chosen to offer good contrast in typical dashboards.
    lines_colors = [
        "#e6007e",  # magenta
        "#a4c238",  # lime green
        "#3cb4e6",  # sky blue
        "#ef4f4f",  # red
        "#46b28e",  # teal/green
        "#e8ce0e",  # yellow
        "#60b562",  # green
        "#f99e20",  # orange
        "#41b3ba",  # turquoise
    ]

    @staticmethod
    def draw_tags_regions(fig, label_time_tags, x_min=None, x_max=None):
        """
        Draw vertical rectangles on a Plotly figure for time-based labeled tags.

        Parameters:
            fig: plotly.graph_objects.Figure
                Target figure to annotate with vertical rectangles.
            label_time_tags: list[dict]
                List of tag entries, each expected to contain:
                    - 'time_start': numeric or datetime indicating start x-position
                    - 'time_end': numeric or datetime indicating end x-position
                    - 'label': text shown at top-left of the rectangle
            x_min: numeric|None
                Lower bound of the plotted data on the x (Time) axis. When provided,
                tag rectangles are clamped to this value and tags ending before it
                are skipped.
            x_max: numeric|None
                Upper bound of the plotted data on the x (Time) axis. When provided,
                tag rectangles are clamped to this value and tags starting after it
                are skipped.

        Behavior:
            - Adds a semi-transparent green rectangle for each tag interval.
            - Places label text at the top-left of each rectangle.
            - Rectangles span the full Y range of the subplot.
            - Clamps rectangles to [x_min, x_max] when those bounds are given, so a
              tag extending beyond the plotted data cannot stretch the x-axis
              autorange (which would distort the zoom level).

        Notes:
            - No validation is performed; ensure keys exist in tag dicts.
            - Use this for simple overlays of annotated time intervals.
        """
        # Add vertical rectangles for labeled data
        for tt in label_time_tags:
            x0 = tt["time_start"]
            x1 = tt["time_end"]

            # Clamp the rectangle to the plotted data range so it does not extend
            # the x-axis autorange (which would zoom the plot out incorrectly).
            if x_min is not None:
                # Skip tags that end before the visible data starts.
                if x1 < x_min:
                    continue
                x0 = max(x0, x_min)
            if x_max is not None:
                # Skip tags that start after the visible data ends.
                if x0 > x_max:
                    continue
                x1 = min(x1, x_max)

            fig.add_vrect(
                x0=x0,
                x1=x1,
                annotation_text=tt["label"],
                annotation_position="top left",
                fillcolor="green",
                opacity=0.25,
                line_width=2,
            )

    @staticmethod
    def draw_regions(
        fig, ss_data_frame, label, color, opacity=0.5, row=None, col=None, show_label=True
    ):
        """
        Draw contiguous flagged regions on a Plotly figure, with support for subplots.

        Parameters:
            fig: plotly.graph_objects.Figure
                The Plotly figure to draw on.
            ss_data_frame: pandas.DataFrame
                Data containing:
                    - 'Time' column used for x placement of start/end boundaries.
                    - A flag column referenced by 'label' with 1.0 indicating active segments.
            label: str
                Column name in ss_data_frame representing flag values (1.0 active).
                Also used as annotation text and legend name.
            color: str
                Color of the region. Can be hex (e.g., '#ff0000')
                or rgba string (e.g., 'rgba(255,0,0,0.5)').
            opacity: float, default=0.5
                Opacity of rectangles.
            row: int|None
                Row index of the subplot (if using make_subplots). If None, applies to primary axes.
            col: int|None
                Column index of the subplot (if using make_subplots).
                If None, applies to primary axes.
            show_label: bool, default=True
                If True, add annotation text on rectangles and a legend entry.

        Behavior:
            - Finds indices where the flag column equals 1.0.
            - Groups contiguous indices into region segments (split on gaps).
            - For each group, computes start/end times from 'Time' column.
            - Adds a vrect for each segment with specified color/opacity.
            - Adds a dummy scatter trace to show a legend entry for the region.

        Subplot targeting:
                - If row/col are provided, xref/yref are set to address that subplot.
                Plotly uses x{col}/y{row} to reference subplot axes sequences.

        Returns:
            None. Mutates the provided figure by adding shapes and traces.

        Edge cases:
            - If no flags are active, no rectangles or legend items are added.
            - If 'Time' values are not sorted, rectangles reflect the DataFrame order.

        Raises:
            KeyError: If 'Time' or the label column does not exist in ss_data_frame.
            TypeError/ValueError: If values are incompatible with Plotly add_vrect.
        """
        # Get indices where flag is active (exactly 1.0).
        # Adjust if using booleans or other encoding.
        true_flag_idxs = ss_data_frame[label].loc[lambda x: x == 1.0].index

        if len(true_flag_idxs) > 0:
            # Split contiguous index arrays into groups where diff != 1 indicates a gap.
            flag_groups = np.split(true_flag_idxs, np.where(np.diff(true_flag_idxs) != 1)[0] + 1)

            # Iterate groups with enumerate to avoid range(len(...)) (Pylint C0200)
            for _, group in enumerate(flag_groups):
                # Determine boundaries in time domain for each contiguous flag group.
                start_flag_time = ss_data_frame.at[group[0], "Time"]
                end_flag_time = ss_data_frame.at[group[-1], "Time"]

                # Determine xref and yref based on subplot row and col.
                # Plotly axes references for subplots use x{col} and y{row} numbering.
                # Primary axes (single subplot) use 'x' and 'y'.
                xref = f"x{col}" if col else "x"
                yref = f"y{row}" if row else "y"

                fig.add_vrect(
                    x0=start_flag_time,
                    x1=end_flag_time,
                    fillcolor=color,
                    opacity=opacity,
                    line_width=0,  # No border to keep rectangles visually subtle
                    annotation_text=label if show_label else "",
                    annotation_position="top left",
                    xref=xref,
                    yref=yref,
                    layer="below",  # Place under traces to not obscure data lines
                )

            if show_label:
                # Add a legend item using a dummy scatter trace.
                # This renders a square marker in the legend with the given label and color.
                fig.add_trace(
                    {
                        "type": "scatter",
                        "x": [None],  # Dummy data for the legend (no points on chart)
                        "y": [None],
                        "mode": "markers",
                        "marker": {"size": 10, "color": color, "symbol": "square"},
                        "name": label,
                        "showlegend": True,
                        "legendgroup": label,
                        "hoverinfo": "skip",  # Prevent hover popups for legend-only trace
                    }
                )

    @staticmethod
    def darken_color(color_hex, percent):
        """
        Darken a hex color by a given percentage.

        Parameters:
            color_hex: str
                Hex color string in the form '#RRGGBB' (case-insensitive).
            percent: int|float
                Percentage by which to darken each channel. Typical range 0-100.

        Returns:
            str: Darkened hex color in the form '#RRGGBB'.

        Behavior:
            - Multiplies each RGB channel by (100 - percent)/100.
            - Values are clamped implicitly by integer conversion to 0..255.

        Edge cases:
            - If percent <= 0, returns original color.
            - If percent >= 100, returns black '#000000'.

        Raises:
            ValueError: If color_hex is not a valid 7-character hex string starting with '#'.
        """
        # Basic validation to prevent slicing errors.
        if not isinstance(color_hex, str) or len(color_hex) != 7 or not color_hex.startswith("#"):
            raise ValueError("color_hex must be a string in the form '#RRGGBB'")

        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)

        # Compute darkened channels with integer arithmetic.
        # Using max(0, ...) is unnecessary due to integer conversion,
        # but percent > 100 would produce negative values, so clamp via max.
        factor = max(0, (100 - percent)) / 100.0
        r_dark = int(r * factor)
        g_dark = int(g * factor)
        b_dark = int(b * factor)

        color_dark = f"#{r_dark:02x}{g_dark:02x}{b_dark:02x}"

        return color_dark
