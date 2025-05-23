"""
Plain vanilla, pure Python trie implementation for name extensions.

Used in Porting Mendeley.

Written for me by Chat GPT. My comments.
"""


class TrieNode:
    """Single trie node."""

    def __init__(self):
        self.children = {}
        self.value = None


class Trie:
    """Special Trie to find longest plausible name from a list of names."""

    def __init__(self):
        self.root = TrieNode()

    def insert(self, key, value=None):
        node = self.root
        for char in key:
            # Insert key with a value of default if key is not in the dictionary.
            # Return the value for key if key is in the dictionary, else default.
            node = node.children.setdefault(char, TrieNode())
        node.value = value if value is not None else key

    def has_key(self, key):
        node = self.root
        for char in key:
            if char not in node.children:
                return False
            node = node.children[char]
        # node.value = value if value is not None else key
        # so this tells us if the stopping poing corresponds to
        # an input key:
        return node.value is not None

    def get(self, key):
        node = self.root
        for char in key:
            node = node.children[char]
        return node.value

    def longest_unique_completion(self, prefix, strict=True):
        node = self.root
        path = prefix
        for char in prefix:
            if char not in node.children:
                raise ValueError(f"Prefix '{prefix}' not found in trie.")
            node = node.children[char]

        if strict and node.value is None:
            raise ValueError(f"Prefix '{prefix}' is not a valid key.")

        longest = prefix if node.value is not None else None

        while len(node.children) == 1:
            # pick out the one and only child, char key and child node
            char, child = next(iter(node.children.items()))
            # add to the path
            path += char
            # move to the child node
            node = child
            # if at an input key, set as current longest found
            if node.value is not None:
                longest = path

        # if longest return it, otherwise return prefix (which in
        # strict mode is now known to be a key)
        return longest or prefix

    def all_extensions(self, prefix):
        """Return all extensions of the prefix in the Trie."""
        # Not actually used in our application, but an
        # important Trie function.
        node = self.root
        for char in prefix:
            if char not in node.children:
                return []  # prefix not in trie
            node = node.children[char]

        results = []

        def collect(n, path):
            if n.value is not None:
                results.append(path)
            for char, child in n.children.items():
                collect(child, path + char)

        collect(node, prefix)
        return results
