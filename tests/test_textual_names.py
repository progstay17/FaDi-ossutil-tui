"""Guard: our screens and app must not overwrite attributes Textual uses internally.

A past bug set self._closed on a screen, which is a Textual message-pump flag, and the
screen then never finished closing (Escape hung the UI). Names are checked statically.
"""
import ast
import inspect
import textwrap

import pytest
from textual.app import App
from textual.screen import ModalScreen, Screen

import ossutil_tui.app as app_module

CLASSES = [
    app_module.AddJobScreen,
    app_module.PreflightScreen,
    app_module.BrowserScreen,
    app_module.LoginScreen,
    app_module.LogoutScreen,
    app_module.MainScreen,
    app_module.FaDiApp,
]


def assigned_self_names(cls):
    names = set()
    for _, member in inspect.getmembers(cls, inspect.isfunction):
        if member.__qualname__.split(".")[-2] != cls.__name__:
            continue
        tree = ast.parse(textwrap.dedent(inspect.getsource(member)))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    names.add(target.attr)
    return names


def textual_base(cls):
    for base in cls.__mro__[1:]:
        if base in (Screen, ModalScreen, App):
            return base
    raise AssertionError(f"{cls.__name__} has no Textual base")


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
def test_no_attribute_collides_with_textual(cls):
    base = textual_base(cls)
    reserved = set(dir(base))
    # Textual's own instance attributes (set in __init__ of the whole MRO) count too.
    for klass in base.__mro__:
        if klass is object or not klass.__module__.startswith("textual"):
            continue
        if "__init__" in vars(klass):
            reserved |= assigned_self_names_of(klass)
    collisions = sorted(assigned_self_names(cls) & reserved)
    assert not collisions, f"{cls.__name__} overwrites Textual attributes: {collisions}"


def assigned_self_names_of(klass):
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(klass.__init__)))
    except (OSError, TypeError):
        return set()
    names = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                names.add(target.attr)
    return names


def test_the_guard_would_have_caught_the_original_bug():
    class Broken(ModalScreen):
        def action_cancel(self):
            self._closed = True

    reserved = set(dir(ModalScreen))
    for klass in ModalScreen.__mro__:
        if klass is not object and klass.__module__.startswith("textual") and "__init__" in vars(klass):
            reserved |= assigned_self_names_of(klass)
    assert "_closed" in (assigned_self_names(Broken) & reserved)