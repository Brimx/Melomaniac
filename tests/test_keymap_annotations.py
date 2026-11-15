import typing
import unittest

from ui.shell import keymap


class KeymapAnnotations(unittest.TestCase):
    def test_type_hints_resolve(self):
        for obj in vars(keymap).values():
            if isinstance(obj, type) and obj.__module__ == keymap.__name__:
                typing.get_type_hints(obj, vars(keymap))


if __name__ == "__main__":
    unittest.main()
