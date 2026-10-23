"""A manually advanced clock; every test creates its own instance."""

class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value
