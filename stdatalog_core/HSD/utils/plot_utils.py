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

import numpy as np

class PlotUtils:
    
    # A list of colors to be used for line plotting, for example in a graph.
    lines_colors = ['#e6007e', '#a4c238', '#3cb4e6', '#ef4f4f', '#46b28e', '#e8ce0e', '#60b562', '#f99e20', '#41b3ba']

    @staticmethod
    def draw_tags_regions(fig, label_time_tags):
        # Add vertical rectangles for labeled data
        for tt in label_time_tags:
            fig.add_vrect(x0=tt["time_start"], x1=tt["time_end"], 
                            annotation_text=tt["label"], annotation_position="top left",
                            fillcolor="green", opacity=0.25, line_width=2)

    @staticmethod
    def draw_regions(fig, ss_data_frame, label, color, opacity=0.5, row=None, col=None, show_label=True):
        """
        Draw regions on a Plotly figure based on flagged data, with support for subplots.

        Args:
            fig (Figure): The Plotly figure to draw on.
            ss_data_frame (DataFrame): The data frame containing the data.
            label (str): The column name in the data frame to use for flags.
            color (str): The color of the region (e.g., 'rgba(255,0,0,0.5)').
            opacity (float): The opacity of the region (default is 0.5).
            row (int, optional): The row of the subplot to draw on (if using subplots).
            col (int, optional): The column of the subplot to draw on (if using subplots).
        """

        true_flag_idxs = ss_data_frame[label].loc[lambda x: x == 1.0].index
        if len(true_flag_idxs) > 0:
            flag_groups = np.split(true_flag_idxs, np.where(np.diff(true_flag_idxs) != 1)[0] + 1)
            for i in range(len(flag_groups)):
                start_flag_time = ss_data_frame.at[flag_groups[i][0], 'Time']
                end_flag_time = ss_data_frame.at[flag_groups[i][-1], 'Time']
                
                # Determine xref and yref based on subplot row and col
                xref = f"x{col}" if col else "x"
                yref = f"y{row}" if row else "y"

                fig.add_vrect(
                    x0=start_flag_time,
                    x1=end_flag_time,
                    fillcolor=color,
                    opacity=opacity,
                    line_width=0,
                    annotation_text = label if show_label else "",
                    annotation_position="top left",
                    xref=xref,
                    yref=yref,
                    layer="below"
                    )
            
            # Add a legend item with a square and the label
            fig.add_trace(
                dict(
                    type="scatter",
                    x=[None],  # Dummy data for the legend
                    y=[None],
                    mode="markers",
                    marker=dict(size=10, color=color, symbol="square"),
                    name=label,
                    showlegend=True,
                    legendgroup=label,
                    hoverinfo="skip"
                )
            )

    @staticmethod
    def darken_color(color_hex, percent):
        r = int(color_hex[1:3], 16)
        g = int(color_hex[3:5], 16)
        b = int(color_hex[5:7], 16)

        r_dark = int(r * (100 - percent) / 100)
        g_dark = int(g * (100 - percent) / 100)
        b_dark = int(b * (100 - percent) / 100)

        color_dark = "#{:02x}{:02x}{:02x}".format(r_dark, g_dark, b_dark)

        return color_dark