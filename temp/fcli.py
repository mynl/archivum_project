# # call python -m fcli -f author_list.md -l 50


from pathlib import Path
import click
from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rapidfuzz import fuzz, process, utils

class FzfApp:
    def __init__(self, choices, limit):
        self.choices = choices
        self.limit = limit if limit > 0 else 10 # Max items to display in results window

        # Initial population of matches (no query yet)
        # Display all choices initially, or up to a reasonable number if choices list is huge
        # For now, let's consider all choices available for initial view, actual display capped by window height
        self.matches = [(s, 0) for s in self.choices] # (text, dummy_score)

        # current_selection_idx: index in self.matches for the highlighted line
        self.current_selection_idx = 0 if self.matches else -1
        self.selected_value = None # Final selected string on Enter

        self.text_area = TextArea(height=1, prompt='> ', multiline=False)
        self.result_control = FormattedTextControl(self._get_matches_fragments) # Renamed for clarity
        self.result_window = Window(
            content=self.result_control,
            height=D(min=1, max=self.limit, weight=1),
            always_hide_cursor=True, # We render our own 'cursor'
            wrap_lines=False
        )

        self.kb = KeyBindings()
        self._register_keys()
        self.text_area.buffer.on_text_changed += self._on_text_input_changed # Renamed for clarity

        self.layout = Layout(HSplit([self.result_window, self.text_area]))

        # Added styles for selection cursor and text
        self.style = Style.from_dict({
            'match': 'ansimagenta bold', # Changed match color for better visibility on default/orange
            'default': 'ansiwhite',
            'prompt': 'ansiblue',
            'selected-gutter': 'bg:ansired ansiwhite bold', # Gutter for selected line (e.g., ">")
            'unselected-gutter': '',                      # Gutter for non-selected lines (e.g., "  ")
            'selected-text': 'ansiyellow',                # Text of selected line (non-matched part)
            'selected-match': 'ansiyellow bold underline',# Matched characters on selected line
        })

        self.application = Application(
            layout=self.layout,
            key_bindings=self.kb,
            full_screen=True,
            style=self.style,
            mouse_support=False, # Explicitly disable mouse if not handled
        )

    def _register_keys(self):
        @self.kb.add('c-c')
        @self.kb.add('c-q')
        def _(event):
            self.selected_value = None # Ensure selection is None on quit
            event.app.exit()

        @self.kb.add('enter')
        def _(event):
            if self.matches and 0 <= self.current_selection_idx < len(self.matches):
                self.selected_value = self.matches[self.current_selection_idx][0]
            else:
                self.selected_value = None
            event.app.exit()

        @self.kb.add('up')
        def _(event):
            if self.matches: # Only navigate if there are matches
                if self.current_selection_idx > 0:
                    self.current_selection_idx -= 1
                # Optional: Wrap around to the bottom
                # else: self.current_selection_idx = len(self.matches) - 1
                self.application.invalidate()

        @self.kb.add('down')
        def _(event):
            if self.matches: # Only navigate if there are matches
                if self.current_selection_idx < len(self.matches) - 1:
                    self.current_selection_idx += 1
                # Optional: Wrap around to the top
                # else: self.current_selection_idx = 0
                self.application.invalidate()

    def _on_text_input_changed(self, unused_buffer_arg):
        query = self.text_area.text
        if query:
            # Using QRatio for potentially better intuitive scoring, as discussed
            # Get all potential matches to allow full navigation
            raw_matches = process.extract(query, self.choices, scorer=fuzz.QRatio,
                                          processor=utils.default_process, limit=len(self.choices))

            self.matches = [(s, score) for s, score, original_index in raw_matches if score > 0] # Filter out 0 score
            # Sort descending by score (higher score = better match = appears at top)
            self.matches.sort(key=lambda x: x[1], reverse=True)
        else:
            # No query, show all choices with a default (or zero) score
            self.matches = [(s, 0) for s in self.choices]
            # No specific sort needed if all scores are 0, original order is kept

        # Reset selection to the top of the new list
        self.current_selection_idx = 0 if self.matches else -1

        self.application.invalidate()

    def _get_matches_fragments(self):
        all_fragments = []
        query_lower = self.text_area.text.lower()

        # We provide fragments for all matches; the Window handles scrolling/clipping.
        for list_idx in range(len(self.matches)):
            original_string, score = self.matches[list_idx]
            is_currently_selected_line = (list_idx == self.current_selection_idx)

            # 1. Gutter
            gutter_text = "> " if is_currently_selected_line else "  "
            gutter_style_class = 'class:selected-gutter' if is_currently_selected_line else 'class:unselected-gutter'
            all_fragments.append((gutter_style_class, gutter_text))

            # 2. Matched string with highlighting
            # Calculate indices of characters to highlight in the original_string
            indices_to_highlight_in_string = set()
            if query_lower: # Only highlight if there's a query
                string_lower = original_string.lower()
                last_found_char_idx = -1
                for query_char_lower in query_lower:
                    try:
                        # Find next occurrence of query_char_lower in string_lower
                        found_char_idx = string_lower.index(query_char_lower, last_found_char_idx + 1)
                        indices_to_highlight_in_string.add(found_char_idx)
                        last_found_char_idx = found_char_idx
                    except ValueError:
                        # Character from query not found sequentially
                        pass

            # Append fragments for each character of the original_string
            for char_idx, char_in_string in enumerate(original_string):
                is_char_part_of_match = char_idx in indices_to_highlight_in_string
                char_style_class = ''

                if is_currently_selected_line:
                    char_style_class = 'class:selected-match' if is_char_part_of_match else 'class:selected-text'
                else: # Line is not selected
                    char_style_class = 'class:match' if is_char_part_of_match else 'class:default'
                all_fragments.append((char_style_class, char_in_string))

            all_fragments.append(('', '\n')) # Newline after each match item

        # Remove the very last newline if it exists to prevent an extra blank line
        if all_fragments and all_fragments[-1] == ('', '\n'):
            all_fragments.pop()

        return all_fragments

    def run(self):
        # Ensure initial selection index is valid
        if not self.matches:
            self.current_selection_idx = -1
        elif self.current_selection_idx == -1: # Should be 0 if matches exist
             self.current_selection_idx = 0

        self.application.run()
        return self.selected_value


@click.command()
@click.option('-f', '--file', 'file_path', type=click.Path(exists=True, path_type=Path), default=None)
@click.option('-l', '--limit', default=10, show_default=True, help='Max number of matches to display in the results area.')
def cli(file_path, limit):
    if file_path:
        choices = file_path.read_text(encoding='utf-8').splitlines()
    else:
        # Read from stdin if no file is provided
        # click.echo("Reading choices from stdin... (Ctrl+D or Ctrl+Z then Enter to end)", err=True)
        stdin_text = click.get_text_stream('stdin').read()
        choices = stdin_text.splitlines()

    if not choices:
        click.echo("No choices provided.", err=True)
        return

    app = FzfApp(choices, limit)
    selection = app.run()
    if selection:
        click.echo(selection)

if __name__ == '__main__':
    cli()

## ~~~~~~~~~~~~~~~~~~version 1~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# from pathlib import Path
# import click
# from prompt_toolkit import Application
# from prompt_toolkit.key_binding import KeyBindings
# from prompt_toolkit.layout import Layout, HSplit, Window
# from prompt_toolkit.layout.controls import FormattedTextControl
# from prompt_toolkit.layout.dimension import D
# from prompt_toolkit.styles import Style
# from prompt_toolkit.widgets import TextArea # Keep TextArea if other parts use its specific features
# # If only for text input, `prompt_toolkit.layout.controls.BufferControl` could also be an option within a Window.
# from rapidfuzz import fuzz, process, utils
# from rapidfuzz.distance import Levenshtein


# class FzfApp:
#     def __init__(self, choices, limit):
#         self.choices = choices
#         self.limit = limit
#         # Initialize with up to 'limit' choices, ensuring not to exceed available choices
#         initial_display_count = min(len(self.choices), self.limit)
#         self.matches = [(s, 100) for s in self.choices[:initial_display_count]]
#         self.selected = None

#         self.text_area = TextArea(height=1, prompt='> ', multiline=False)

#         self.result_control = FormattedTextControl(self._get_matches)
#         self.result_window = Window(
#             content=self.result_control,
#             # Ensure result_window can shrink if less than 'limit' items initially or after filtering
#             height=D(min=1, max=self.limit, weight=1), # Adjusted height
#             always_hide_cursor=True,
#             wrap_lines=False # Usually desired for fzf like display
#         )

#         self.kb = KeyBindings()
#         self._register_keys()
#         self.text_area.buffer.on_text_changed += self._on_text_change

#         self.layout = Layout(
#             HSplit([
#                 # The "fill" window above is not strictly necessary unless you want to push results down.
#                 # For fzf, results are typically at the top or bottom, adjacent to the prompt.
#                 # If results are dynamic, the layout should accommodate this.
#                 # Window(height=D(weight=1), char=' '), # This pushes content down.
#                 self.result_window,
#                 self.text_area,
#             ])
#         )

#         self.style = Style.from_dict({
#             'match': 'ansigreen bold',
#             'default': 'ansiwhite',
#             # prompt_toolkit uses 'prompt' style for TextArea's prompt
#             'prompt': 'ansiblue',
#         })

#         self.application = Application(
#             layout=self.layout,
#             key_bindings=self.kb,
#             full_screen=True,
#             style=self.style,
#             # Ensure mouse support is False if not handled, or True if handled
#             mouse_support=False,
#         )

#     def _register_keys(self):
#         @self.kb.add('c-c')
#         @self.kb.add('c-q')
#         def _(event):
#             event.app.exit()

#         @self.kb.add('enter')
#         def _(event):
#             if self.matches:
#                 # Selects the last item, assuming it's the best match after sorting
#                 self.selected = self.matches[-1][0]
#             event.app.exit()

#     def _on_text_change(self, _):
#         query = self.text_area.text
#         if query:
#             # Use processor=utils.default_process explicitly if you want RapidFuzz's default
#             # The choices are already strings, no need to process them here again unless desired

#             # original
#             # raw = process.extract(query, self.choices, scorer=fuzz.WRatio, processor=utils.default_process, limit=self.limit)

#             # alt 1 qratio
#             raw = process.extract(query, self.choices, scorer=fuzz.QRatio, processor=utils.default_process, limit=self.limit)

#             # alt 2 token sort ratio
#             # raw = process.extract(query, self.choices, scorer=fuzz.token_sort_ratio, processor=utils.default_process, limit=self.limit)

#             self.matches = [(s, score) for s, score, original_index in raw]
#         else:
#             initial_display_count = min(len(self.choices), self.limit)
#             self.matches = [(s, 100) for s in self.choices[:initial_display_count]]

#         # Sort by score. WRatio: higher is better. So sort descending for best matches first.
#         # If you want best match last (for Enter to pick from bottom of visible list):
#         self.matches.sort(key=lambda x: x[1])
#         # If you want best match first (top of visible list):
#         # self.matches.sort(key=lambda x: x[1], reverse=True)

#         self.application.invalidate()  # Corrected: invalidate the application

#     def _get_matches(self):
#         query = self.text_area.text

#         # Number of actual matches to display
#         num_current_matches = len(self.matches)
#         # Number of padding lines needed at the top if list is shorter than limit
#         # This depends on how you want to display: fzf typically fills from top.
#         # If you fix the window height to 'limit', then padding might be at bottom or not needed.
#         # For dynamic height, padding concept might change.
#         # Assuming we want to show up to `self.limit` lines, padding if fewer matches.
#         pad = max(0, self.limit - num_current_matches)

#         all_fragments = [] # This will be a flat List[Tuple[str,str]]

#         # This padding adds empty lines at the top if result_window height is fixed.
#         # If result_window height is dynamic, this might not be desired.
#         # For now, let's assume we want to fill up to 'limit' lines.
#         for _ in range(pad):
#             all_fragments.append(('', '\n'))

#         for s, score in self.matches: # s is an original choice string
#             # Process strings for highlighting comparison
#             # Note: `s` is the original choice string. `query` is from text_area.
#             norm_s = utils.default_process(s)
#             norm_q = utils.default_process(query)

#             current_s_fragments = [] # Fragments for the current line `s`

#             # --- Highlighting Logic ---
#             # The original highlighting logic:
#             # ops = Levenshtein.editops(norm_q, norm_s)
#             # idxs_in_norm_s = {j for (op, _, j) in ops if op == 'delete'}
#             # This logic is potentially flawed for typical fuzzy match highlighting and index mapping.
#             #
#             # For a more fzf-like greedy highlighting of query characters in choice:
#             idxs_in_s_to_highlight = set()
#             if norm_q and norm_s: # Only attempt highlighting if query and processed choice are non-empty
#                 s_iter_idx = 0
#                 # Map query characters to their first appearance in `s` (case-insensitive via norm_s, norm_q)
#                 # This is a simple greedy approach.
#                 # We need to map indices from norm_s back to s if structure changes.
#                 # For a direct approach on original `s` (but using `norm_q` for case-insensitivity):

#                 # Create mapping from original `s` indices to `norm_s` indices
#                 s_to_norm_s_map = {} # s_idx -> norm_s_idx
#                 norm_s_idx_counter = 0
#                 temp_reconstructed_norm_s = []
#                 for i, char_original_s in enumerate(s):
#                     processed_char = utils.default_process(char_original_s) # Process char by char
#                     if processed_char: # If char is not removed by processing
#                         # This simplistic char-by-char processing might not perfectly match `utils.default_process(s)`
#                         # A more robust way is needed if `default_process` merges or splits characters based on context.
#                         # Assuming default_process operates mostly on individual chars for this mapping:
#                         for p_char in processed_char: # processed_char could be multiple chars if rules are complex
#                             s_to_norm_s_map[i] = norm_s_idx_counter # Map original index to first processed char index
#                                                                    # This map is imperfect if one orig char -> multiple proc chars or vice versa
#                             temp_reconstructed_norm_s.append(p_char)
#                             norm_s_idx_counter +=1

#                 # A truly robust mapping requires aligning s and norm_s.
#                 # For now, let's use a simpler highlighting based on finding norm_q chars in norm_s, then trying to map indices.
#                 # This part remains complex to get perfectly robust with default_process.

#                 # Fallback to simple greedy highlighting on `s` using `norm_q`:
#                 last_found_s_idx = -1
#                 norm_s_lower = s.lower() # Compare against lowercased original string
#                 norm_q_lower = query.lower()

#                 for q_char_lower in norm_q_lower:
#                     try:
#                         # Find in s.lower() starting after last match
#                         # This highlights based on original string 's'
#                         found_s_idx = norm_s_lower.index(q_char_lower, last_found_s_idx + 1)
#                         idxs_in_s_to_highlight.add(found_s_idx)
#                         last_found_s_idx = found_s_idx
#                     except ValueError:
#                         pass # Character not found

#             for i, char_s in enumerate(s):
#                 style = 'class:match' if i in idxs_in_s_to_highlight else 'class:default'
#                 current_s_fragments.append((style, char_s))

#             all_fragments.extend(current_s_fragments)
#             all_fragments.append(('', '\n'))

#         if all_fragments and all_fragments[-1] == ('', '\n'):
#             all_fragments.pop() # Remove the very last newline if it exists

#         return all_fragments

#     def run(self):
#         self.application.run()
#         return self.selected


# @click.command()
# @click.option('-f', '--file', 'file_path', type=click.Path(exists=True, path_type=Path), default='author_list.md')
# @click.option('-l', '--limit', default=10, show_default=True, help='Number of matches to show')
# def cli(file_path, limit):
#     choices = file_path.read_text(encoding='utf-8').splitlines()
#     if not choices:
#         click.echo("No choices found in the file.", err=True)
#         return
#     app = FzfApp(choices, limit)
#     selection = app.run()
#     if selection:
#         click.echo(selection)

# if __name__ == '__main__':
#     cli()



## ======================GPT================================
## ======================GPT================================
## ======================GPT================================
# # fzfcli.py
# from pathlib import Path
# import click
# from prompt_toolkit import Application
# from prompt_toolkit.key_binding import KeyBindings
# from prompt_toolkit.layout import Layout, HSplit, Window
# from prompt_toolkit.layout.controls import FormattedTextControl
# from prompt_toolkit.layout.dimension import D
# from prompt_toolkit.styles import Style
# from prompt_toolkit.widgets import TextArea
# from rapidfuzz import fuzz, process, utils
# from rapidfuzz.distance import Levenshtein


# class FzfApp:
#     def __init__(self, choices, limit):
#         self.choices = choices
#         self.limit = limit
#         self.matches = [(s, 100) for s in choices[:limit]]
#         self.selected = None

#         self.text_area = TextArea(height=1, prompt='> ', multiline=False)

#         # Display buffer: FormattedTextControl will call a method to get lines
#         self.result_control = FormattedTextControl(self._get_matches)
#         self.result_window = Window(
#             content=self.result_control,
#             height=D(weight=1),
#             always_hide_cursor=True
#         )

#         self.kb = KeyBindings()
#         self._register_keys()
#         # Bind text change
#         self.text_area.buffer.on_text_changed += self._on_text_change

#         # self._bind_on_text_change()

#         self.layout = Layout(
#             HSplit([
#                 # “Fill” all the extra space above
#                 Window(height=D(weight=1), char=' '),
#                 self.result_window,
#                 self.text_area,
#             ])
#         )

#         self.style = Style.from_dict({
#             'match': 'ansiyellow bold',
#             'default': 'ansiwhite',
#         })

#         self.application = Application(
#             layout=self.layout,
#             key_bindings=self.kb,
#             full_screen=True,
#             style=self.style,
#         )

#     def _register_keys(self):
#         @self.kb.add('c-c')
#         @self.kb.add('c-q')
#         def _(event):
#             event.app.exit()

#         @self.kb.add('enter')
#         def _(event):
#             if self.matches:
#                 self.selected = self.matches[-1][0]
#             event.app.exit()

#     def _on_text_change(self, _):
#         query = self.text_area.text
#         if query:
#             raw = process.extract(query, self.choices, scorer=fuzz.WRatio, limit=self.limit)
#             self.matches = [(s, score) for s, score, _ in raw]
#         else:
#             self.matches = [(s, 100) for s in self.choices[:self.limit]]

#         self.matches.sort(key=lambda x: x[1])  # best match last
#         self.result_control.invalidate()       # trigger redraw


#     def _get_matches(self):
#         query = self.text_area.text
#         pad = max(0, self.limit - len(self.matches))

#         lines = []

#         for _ in range(pad):
#             lines.append([('', '')])  # each line must be a list of (style, text)

#         for s, _ in self.matches:
#             norm_s = utils.default_process(s)
#             norm_q = utils.default_process(query)
#             if norm_s and norm_q:
#                 ops = Levenshtein.editops(norm_q, norm_s)
#                 idxs = {j for (op, _, j) in ops if op == 'delete'}
#             else:
#                 idxs = set()

#             styled_line = []
#             for i, c in enumerate(s):
#                 style = 'class:match' if i in idxs else 'class:default'
#                 styled_line.append([(style, c)])
#             lines.append(styled_line)  # <-- this must be a list, not a tuple

#         return lines



#     def run(self):
#         self.application.run()
#         return self.selected


# @click.command()
# @click.option('-f', '--file', 'file_path', type=click.Path(exists=True, path_type=Path), default='author_list.md')
# @click.option('-l', '--limit', default=10, show_default=True, help='Number of matches to show')
# def cli(file_path, limit):
#     choices = file_path.read_text(encoding='utf-8').splitlines()
#     selection = FzfApp(choices, limit).run()
#     if selection:
#         click.echo(selection)


# if __name__ == '__main__':
#     cli()


# """GPT generated roll-your-own fzf template."""

# from pathlib import Path

# # fzfcli.py
# import click
# from rapidfuzz import fuzz, process, utils
# from rapidfuzz.distance import Levenshtein
# from prompt_toolkit import Application
# # from prompt_toolkit.buffer import Buffer
# from prompt_toolkit.key_binding import KeyBindings
# from prompt_toolkit.layout import Layout, HSplit, Window # , VSplit
# from prompt_toolkit.layout.controls import FormattedTextControl
# from prompt_toolkit.layout.dimension import D
# from prompt_toolkit.styles import Style
# from prompt_toolkit.widgets import TextArea

# CHOICES = 50

# class FzfApp:
#     def __init__(self, choices):
#         self.choices = choices
#         self.matches = [(s, 100) for s in choices[:CHOICES]]
#         self.selected = None

#         self.text_area = TextArea(height=1, prompt='> ', multiline=False)

#         # Display buffer: FormattedTextControl will call a method to get lines
#         self.result_control = FormattedTextControl(text=self._get_formatted_matches)
#         self.result_window = Window(
#             content=self.result_control,
#             height=D(weight=1),
#             always_hide_cursor=True
#         )

#         self.kb = KeyBindings()
#         self._register_keys()

#         self._bind_on_text_change()

#         self.layout = Layout(
#             HSplit([
#                 self.result_window,
#                 self.text_area,
#             ])
#         )

#         self.style = Style.from_dict({
#             'match': 'ansiyellow bold',
#             'default': '',
#         })

#         self.application = Application(
#             layout=self.layout,
#             key_bindings=self.kb,
#             full_screen=True,
#             style=self.style
#         )

#     def _register_keys(self):
#         @self.kb.add("c-c")
#         @self.kb.add("c-q")
#         def _(event):
#             event.app.exit()

#         @self.kb.add("enter")
#         def _(event):
#             if self.matches:
#                 self.selected = self.matches[-1][0]  # best match is last
#             event.app.exit()

#     def _bind_on_text_change(self):
#         def handler(_):
#             self._update_matches()
#             self.result_control.text = self._get_formatted_matches()
#         self.text_area.buffer.on_text_changed += handler

#     def _update_matches(self):
#         query = self.text_area.text
#         if query:
#             self.matches = [
#                 (s, score) for s, score, _ in process.extract(
#                     query, self.choices, scorer=fuzz.WRatio, limit=CHOICES
#                 )
#             ]
#             # Sort so best match is last (for bottom alignment)
#             self.matches.sort(key=lambda x: x[1])
#         else:
#             self.matches = [(s, 100) for s in self.choices[:CHOICES]]

#     def _get_formatted_matches(self):
#         query = self.text_area.text
#         lines = []

#         # Pad to bottom-align
#         pad_lines = CHOICES - len(self.matches)
#         lines.extend([('', '\n')] * pad_lines)

#         for s, _ in self.matches:
#             lines.extend(self._highlight_match(s, query))
#             lines.append(('', '\n'))
#         return lines

#     def _highlight_match(self, choice, query):
#         norm_choice = utils.default_process(choice)
#         norm_query = utils.default_process(query)
#         if not norm_choice or not norm_query:
#             return [('', choice)]

#         ops = Levenshtein.editops(norm_query, norm_choice)
#         match_idxs = {j for (op, _, j) in ops if op == 'delete'}

#         result = []
#         for i, c in enumerate(choice):
#             style = 'class:match' if i in match_idxs else 'class:default'
#             result.append((style, c))
#         return result

#     def run(self):
#         self.application.run()
#         return self.selected

# @click.command()
# # @click.argument('choices', nargs=-1)
# def cli():
#     choices = Path('author_list.md').read_text('utf-8').split('\n')
#     app = FzfApp(list(choices))
#     result = app.run()
#     if result:
#         click.echo(result)

# if __name__ == '__main__':
#     cli()


# # import click

# # from rapidfuzz import fuzz, process, utils
# # from rapidfuzz.distance import Levenshtein


# # from prompt_toolkit import Application
# # from prompt_toolkit.key_binding import KeyBindings
# # from prompt_toolkit.layout import Layout, HSplit, Window
# # from prompt_toolkit.layout.controls import FormattedTextControl
# # from prompt_toolkit.styles import Style
# # from prompt_toolkit.widgets import TextArea
# # # from prompt_toolkit.buffer import Buffer
# # # from prompt_toolkit.filters import Condition
# # # from prompt_toolkit.layout.dimension import D


# # def highlight_match(choice, query):
# #     norm_choice = utils.default_process(choice)
# #     norm_query = utils.default_process(query)
# #     if not norm_choice or not norm_query:
# #         return [('', choice)]

# #     ops = Levenshtein.editops(norm_query, norm_choice)
# #     match_idxs = {j for (op, _, j) in ops if op == 'delete'}

# #     result = []
# #     for i, c in enumerate(choice):
# #         if i in match_idxs:
# #             result.append(('class:match', c))
# #         else:
# #             result.append(('', c))
# #     return result


# # class FzfApp:
# #     def __init__(self, choices):
# #         self.choices = choices
# #         self.matches = [(s, 100) for s in choices[:10]]
# #         self.selected = None

# #         self.text_area = TextArea(height=1, prompt='> ', multiline=False)
# #         self.result_window = Window(
# #             content=FormattedTextControl(text=self._get_formatted_matches),
# #             always_hide_cursor=True,
# #         )

# #         self.kb = KeyBindings()

# #         @self.kb.add("c-c")
# #         @self.kb.add("c-q")
# #         def exit_(event):
# #             event.app.exit()

# #         @self.kb.add("enter")
# #         def select_(event):
# #             if self.matches:
# #                 self.selected = self.matches[0][0]
# #             event.app.exit()

# #         # @self.text_area.buffer.on_text_changed
# #         # def on_change(_):
# #         #     self._update_matches()

# #         self.text_area.buffer.on_text_changed += lambda _: self._update_matches()

# #         self.layout = Layout(
# #             HSplit([
# #                 self.result_window,
# #                 self.text_area,
# #             ])
# #         )

# #         self.style = Style.from_dict({
# #             'match': 'ansiyellow bold',
# #         })

# #         self.application = Application(
# #             layout=self.layout,
# #             key_bindings=self.kb,
# #             full_screen=True,
# #             style=self.style
# #         )

# #     def _update_matches(self):
# #         text = self.text_area.text
# #         if text:
# #             self.matches = [
# #                 (s, score) for s, score, _ in process.extract(
# #                     text, self.choices, scorer=fuzz.WRatio, limit=10
# #                 )
# #             ]
# #         else:
# #             self.matches = [(s, 100) for s in self.choices[:10]]

# #     def _get_formatted_matches(self):
# #         query = self.text_area.text
# #         lines = []
# #         for s, _ in self.matches:
# #             lines.extend(highlight_match(s, query))
# #             lines.append(('', '\n'))
# #         return lines

# #     def run(self):
# #         self.application.run()
# #         return self.selected

# # @click.command()
# # @click.argument('choices', nargs=-1)
# # def cli(choices):
# #     app = FzfApp(list(choices))
# #     result = app.run()
# #     if result:
# #         click.echo(result)

# # if __name__ == '__main__':
# #     cli()
