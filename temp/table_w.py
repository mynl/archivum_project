"""Determine optimal column widths."""


import pandas as pd


def optimize_column_widths(self, overall_width_constraint: float) -> dict[str, float]:
    """
    Optimize column widths for a Pandas DataFrame given an overall width constraint.

    Widths are in abstract character units.

    Args:
        df: The input Pandas DataFrame.
        breakable_cols: A dictionary where keys are column names and values are booleans.
                        True if column content can wrap (text), False otherwise (numbers/fixed).
        overall_width_constraint: The total available width for the table (in abstract units).

    Returns:
        A dictionary mapping column names to their optimized widths (in abstract units).

    Raises:
        ValueError: If a column in the DataFrame is not found in the breakable_cols mapping.

    Gemini code
    """
    # df we will work on: this has had all formatting applied (??string pruning?)
    # all dtypes should be object
    df = self.df.copy()
    assert all([i==object for i in df.dtypes.values])

    col_widths = {}
    # The absolute minimum width each column can take (e.g., longest word for text)
    min_possible_widths = {}
    # The width if content didn't wrap (single line)
    # Series=dict colname->max width of cells in column
    ideal_widths = df.map(len).max(axis=0).to_dict()
    # map break penalties to True (strings) / False (numbers and dates)
    breakable_cols = dict(zip(df.columns, [True if i < 5 else False for i in self.break_penalties]))
    print(self.break_penalties)
    print(breakable_cols)

    # 1. Calculate ideal (no wrap) and minimum possible widths for all columns
    for col_name in df.columns:
        if col_name not in breakable_cols:
            raise ValueError(f"Column '{col_name}' not found in breakable_cols mapping. Please provide a boolean for every column.")

        max_len = ideal_widths[col_name]

        if breakable_cols[col_name]:
            # For breakable text, min width is the longest word, or a small default
            # Estimate the minimum unbreakable width for a text column.
            min_possible_widths[col_name] = (
                df[col_name].str
                    .split(pat='[^\w]', regex=True, expand=True)
                    .fillna('')
                    .map(len)
                    .max(axis=1)
                    .max()
                    )
        else:
            # For non-breakable content, min width is its ideal width
            min_possible_widths[col_name] = max_len

        # Ensure a minimum width of 1 unit for all columns, even if content is empty
        if min_possible_widths[col_name] == 0:
            min_possible_widths[col_name] = 1

    total_ideal_width = sum(ideal_widths.values())
    total_min_possible_width = sum(min_possible_widths.values())

    # 2. Distribute width based on overall_width_constraint
    if total_ideal_width <= overall_width_constraint:
        # We have enough space for ideal widths (no wrapping).
        # Assign ideal widths and distribute any remaining space proportionally.
        col_widths = {col: ideal_widths[col] for col in df.columns}
        remaining_space = overall_width_constraint - total_ideal_width

        if remaining_space > 0 and total_ideal_width > 0:
            # Distribute remaining space proportionally to current ideal widths
            proportion_factor = remaining_space / total_ideal_width
            for col in df.columns:
                col_widths[col] += col_widths[col] * proportion_factor
        elif remaining_space > 0 and total_ideal_width == 0 and len(df.columns) > 0:
            # Handle case where all ideal widths are zero (e.g., empty DataFrame)
            # Distribute space equally
            equal_share = overall_width_constraint / len(df.columns)
            for col in df.columns:
                col_widths[col] = equal_share

    else:
        # We need to shrink. Total ideal width exceeds the constraint.
        # This is where the heuristic comes in.

        if overall_width_constraint < total_min_possible_width:
            # The constraint is tighter than even the absolute minimums.
            # In this case, we have to scale down even the minimums. This will
            # likely lead to content truncation or severe wrapping.
            if total_min_possible_width > 0:
                scale_factor = overall_width_constraint / total_min_possible_width
                for col in df.columns:
                    col_widths[col] = min_possible_widths[col] * scale_factor
            elif len(df.columns) > 0:  # All min widths are zero, distribute equally
                equal_share = overall_width_constraint / len(df.columns)
                for col in df.columns:
                    col_widths[col] = equal_share
            else:  # No columns to distribute width to
                return {}  # Empty dictionary
        else:
            # We can fit the minimums, but not all ideals.
            # Assign minimum widths first.
            col_widths = {col: min_possible_widths[col] for col in df.columns}
            remaining_space = overall_width_constraint - total_min_possible_width

            # Identify columns that can expand from their minimums up to their ideal widths.
            expandable_cols_capacity = {
                col: ideal_widths[col] - min_possible_widths[col]
                for col in df.columns
                if ideal_widths[col] > min_possible_widths[col]
            }
            total_expandable_capacity = sum(expandable_cols_capacity.values())

            if remaining_space > 0 and total_expandable_capacity > 0:
                # Distribute the `remaining_space` among expandable columns.
                # We distribute proportionally based on their *capacity to expand*.
                # This ensures columns that *need* more space (to reach ideal) get more of the available extra space.
                distribute_factor = min(1.0, remaining_space / total_expandable_capacity)

                for col in df.columns:
                    if col in expandable_cols_capacity:
                        col_widths[col] += expandable_cols_capacity[col] * distribute_factor

    # Round widths to a sensible number of decimal places for practical use
    for col in col_widths:
        col_widths[col] = round(col_widths[col], 2)

    if 0:
        _debug = pd.DataFrame({
            'breakable_cols': breakable_cols.values(),
            'min_possible_widths': min_possible_widths.values(),
            'ideal_widths': ideal_widths.values(),
            'col_widths': col_widths.values(),
            }, index=df.columns)
        # _debug.loc['total'] = _debug.sum(axis=0)
        print(_debug)
        print(_debug.iloc[:, 1:].sum(0))
    return col_widths


def __str__(self, w):
    """String representation, for print()."""
    if self.df.empty:
        return ""
    colw = optimize_column_widths(self, w)
    # strip off grt- from aligners
    dfa = [i[4:] for i in self.df_aligners]
    return self.df.to_markdown(
        index=self.show_index,
        colalign=dfa,
        tablefmt=self.str_table_fmt,
        maxcolwidths=[colw.get(i) for i in self.df.columns],
    )
