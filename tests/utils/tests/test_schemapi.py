# The contents of this file are automatically written by
# tools/generate_schema_wrapper.py. Do not modify directly.
import copy
import io
import json
import jsonschema
import re
import pickle
import warnings

import numpy as np
import pandas as pd
import pytest
from vega_datasets import data

import altair as alt
from altair import load_schema
from altair.utils.schemapi import (
    UndefinedType,
    SchemaBase,
    Undefined,
    _FromDict,
    SchemaValidationError,
)

_JSONSCHEMA_DRAFT = load_schema()["$schema"]
# Make tests inherit from _TestSchema, so that when we test from_dict it won't
# try to use SchemaBase objects defined elsewhere as wrappers.


class _TestSchema(SchemaBase):
    @classmethod
    def _default_wrapper_classes(cls):
        return _TestSchema.__subclasses__()


class MySchema(_TestSchema):
    _schema = {
        "$schema": _JSONSCHEMA_DRAFT,
        "definitions": {
            "StringMapping": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "StringArray": {"type": "array", "items": {"type": "string"}},
        },
        "properties": {
            "a": {"$ref": "#/definitions/StringMapping"},
            "a2": {"type": "object", "additionalProperties": {"type": "number"}},
            "b": {"$ref": "#/definitions/StringArray"},
            "b2": {"type": "array", "items": {"type": "number"}},
            "c": {"type": ["string", "number"]},
            "d": {
                "anyOf": [
                    {"$ref": "#/definitions/StringMapping"},
                    {"$ref": "#/definitions/StringArray"},
                ]
            },
            "e": {"items": [{"type": "string"}, {"type": "string"}]},
        },
    }


class StringMapping(_TestSchema):
    _schema = {"$ref": "#/definitions/StringMapping"}
    _rootschema = MySchema._schema


class StringArray(_TestSchema):
    _schema = {"$ref": "#/definitions/StringArray"}
    _rootschema = MySchema._schema


class Derived(_TestSchema):
    _schema = {
        "$schema": _JSONSCHEMA_DRAFT,
        "definitions": {
            "Foo": {"type": "object", "properties": {"d": {"type": "string"}}},
            "Bar": {"type": "string", "enum": ["A", "B"]},
        },
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "string"},
            "c": {"$ref": "#/definitions/Foo"},
        },
    }


class Foo(_TestSchema):
    _schema = {"$ref": "#/definitions/Foo"}
    _rootschema = Derived._schema


class Bar(_TestSchema):
    _schema = {"$ref": "#/definitions/Bar"}
    _rootschema = Derived._schema


class SimpleUnion(_TestSchema):
    _schema = {
        "$schema": _JSONSCHEMA_DRAFT,
        "anyOf": [{"type": "integer"}, {"type": "string"}],
    }


class DefinitionUnion(_TestSchema):
    _schema = {"anyOf": [{"$ref": "#/definitions/Foo"}, {"$ref": "#/definitions/Bar"}]}
    _rootschema = Derived._schema


class SimpleArray(_TestSchema):
    _schema = {
        "$schema": _JSONSCHEMA_DRAFT,
        "type": "array",
        "items": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
    }


class InvalidProperties(_TestSchema):
    _schema = {
        "$schema": _JSONSCHEMA_DRAFT,
        "type": "object",
        "properties": {"for": {}, "as": {}, "vega-lite": {}, "$schema": {}},
    }


class Draft7Schema(_TestSchema):
    _schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": {
            "e": {"items": [{"type": "string"}, {"type": "string"}]},
        },
    }


class Draft202012Schema(_TestSchema):
    _schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "e": {"items": [{"type": "string"}, {"type": "string"}]},
        },
    }


def test_construct_multifaceted_schema():
    dct = {
        "a": {"foo": "bar"},
        "a2": {"foo": 42},
        "b": ["a", "b", "c"],
        "b2": [1, 2, 3],
        "c": 42,
        "d": ["x", "y", "z"],
        "e": ["a", "b"],
    }

    myschema = MySchema.from_dict(dct)
    assert myschema.to_dict() == dct

    myschema2 = MySchema(**dct)
    assert myschema2.to_dict() == dct

    assert isinstance(myschema.a, StringMapping)
    assert isinstance(myschema.a2, dict)
    assert isinstance(myschema.b, StringArray)
    assert isinstance(myschema.b2, list)
    assert isinstance(myschema.d, StringArray)


def test_schema_cases():
    assert Derived(a=4, b="yo").to_dict() == {"a": 4, "b": "yo"}
    assert Derived(a=4, c={"d": "hey"}).to_dict() == {"a": 4, "c": {"d": "hey"}}
    assert Derived(a=4, b="5", c=Foo(d="val")).to_dict() == {
        "a": 4,
        "b": "5",
        "c": {"d": "val"},
    }
    assert Foo(d="hello", f=4).to_dict() == {"d": "hello", "f": 4}

    assert Derived().to_dict() == {}
    assert Foo().to_dict() == {}

    with pytest.raises(jsonschema.ValidationError):
        # a needs to be an integer
        Derived(a="yo").to_dict()

    with pytest.raises(jsonschema.ValidationError):
        # Foo.d needs to be a string
        Derived(c=Foo(4)).to_dict()

    with pytest.raises(jsonschema.ValidationError):
        # no additional properties allowed
        Derived(foo="bar").to_dict()


def test_round_trip():
    D = {"a": 4, "b": "yo"}
    assert Derived.from_dict(D).to_dict() == D

    D = {"a": 4, "c": {"d": "hey"}}
    assert Derived.from_dict(D).to_dict() == D

    D = {"a": 4, "b": "5", "c": {"d": "val"}}
    assert Derived.from_dict(D).to_dict() == D

    D = {"d": "hello", "f": 4}
    assert Foo.from_dict(D).to_dict() == D


def test_from_dict():
    D = {"a": 4, "b": "5", "c": {"d": "val"}}
    obj = Derived.from_dict(D)
    assert obj.a == 4
    assert obj.b == "5"
    assert isinstance(obj.c, Foo)


def test_simple_type():
    assert SimpleUnion(4).to_dict() == 4


def test_simple_array():
    assert SimpleArray([4, 5, "six"]).to_dict() == [4, 5, "six"]
    assert SimpleArray.from_dict(list("abc")).to_dict() == list("abc")


def test_definition_union():
    obj = DefinitionUnion.from_dict("A")
    assert isinstance(obj, Bar)
    assert obj.to_dict() == "A"

    obj = DefinitionUnion.from_dict("B")
    assert isinstance(obj, Bar)
    assert obj.to_dict() == "B"

    obj = DefinitionUnion.from_dict({"d": "yo"})
    assert isinstance(obj, Foo)
    assert obj.to_dict() == {"d": "yo"}


def test_invalid_properties():
    dct = {"for": 2, "as": 3, "vega-lite": 4, "$schema": 5}
    invalid = InvalidProperties.from_dict(dct)
    assert invalid["for"] == 2
    assert invalid["as"] == 3
    assert invalid["vega-lite"] == 4
    assert invalid["$schema"] == 5
    assert invalid.to_dict() == dct


def test_undefined_singleton():
    assert Undefined is UndefinedType()


def test_schema_validator_selection():
    # Tests if the correct validator class is chosen based on the $schema
    # property in the schema. Reason for the AttributeError below is, that Draft 2020-12
    # introduced changes to the "items" keyword, see
    # https://json-schema.org/draft/2020-12/release-notes.html#changes-to-
    # items-and-additionalitems
    dct = {
        "e": ["a", "b"],
    }

    assert Draft7Schema.from_dict(dct).to_dict() == dct
    with pytest.raises(AttributeError, match="'list' object has no attribute 'get'"):
        Draft202012Schema.from_dict(dct)


@pytest.fixture
def dct():
    return {
        "a": {"foo": "bar"},
        "a2": {"foo": 42},
        "b": ["a", "b", "c"],
        "b2": [1, 2, 3],
        "c": 42,
        "d": ["x", "y", "z"],
    }


def test_copy_method(dct):
    myschema = MySchema.from_dict(dct)

    # Make sure copy is deep
    copy = myschema.copy(deep=True)
    copy["a"]["foo"] = "new value"
    copy["b"] = ["A", "B", "C"]
    copy["c"] = 164
    assert myschema.to_dict() == dct

    # If we ignore a value, changing the copy changes the original
    copy = myschema.copy(deep=True, ignore=["a"])
    copy["a"]["foo"] = "new value"
    copy["b"] = ["A", "B", "C"]
    copy["c"] = 164
    mydct = myschema.to_dict()
    assert mydct["a"]["foo"] == "new value"
    assert mydct["b"][0] == dct["b"][0]
    assert mydct["c"] == dct["c"]

    # If copy is not deep, then changing copy below top level changes original
    copy = myschema.copy(deep=False)
    copy["a"]["foo"] = "baz"
    copy["b"] = ["A", "B", "C"]
    copy["c"] = 164
    mydct = myschema.to_dict()
    assert mydct["a"]["foo"] == "baz"
    assert mydct["b"] == dct["b"]
    assert mydct["c"] == dct["c"]


def test_copy_module(dct):
    myschema = MySchema.from_dict(dct)

    cp = copy.deepcopy(myschema)
    cp["a"]["foo"] = "new value"
    cp["b"] = ["A", "B", "C"]
    cp["c"] = 164
    assert myschema.to_dict() == dct


def test_attribute_error():
    m = MySchema()
    with pytest.raises(AttributeError) as err:
        m.invalid_attribute
    assert str(err.value) == (
        "'MySchema' object has no attribute " "'invalid_attribute'"
    )


def test_to_from_json(dct):
    json_str = MySchema.from_dict(dct).to_json()
    new_dct = MySchema.from_json(json_str).to_dict()

    assert new_dct == dct


def test_to_from_pickle(dct):
    myschema = MySchema.from_dict(dct)
    output = io.BytesIO()
    pickle.dump(myschema, output)
    output.seek(0)
    myschema_new = pickle.load(output)

    assert myschema_new.to_dict() == dct


def test_class_with_no_schema():
    class BadSchema(SchemaBase):
        pass

    with pytest.raises(ValueError) as err:
        BadSchema(4)
    assert str(err.value).startswith("Cannot instantiate object")


@pytest.mark.parametrize("use_json", [True, False])
def test_hash_schema(use_json):
    classes = _TestSchema._default_wrapper_classes()

    for cls in classes:
        hsh1 = _FromDict.hash_schema(cls._schema, use_json=use_json)
        hsh2 = _FromDict.hash_schema(cls._schema, use_json=use_json)
        assert hsh1 == hsh2
        assert hash(hsh1) == hash(hsh2)


def test_schema_validation_error():
    try:
        MySchema(a={"foo": 4})
        the_err = None
    except jsonschema.ValidationError as err:
        the_err = err

    assert isinstance(the_err, SchemaValidationError)
    message = str(the_err)

    assert message.startswith("Invalid specification")
    assert "test_schemapi.MySchema->a" in message
    assert "validating {!r}".format(the_err.validator) in message
    assert the_err.message in message


def chart_example_layer():
    points = (
        alt.Chart(data.cars.url)
        .mark_point()
        .encode(
            x="Horsepower:Q",
            y="Miles_per_Gallon:Q",
        )
    )
    return (points & points).properties(width=400)


def chart_example_hconcat():
    source = data.cars()
    points = (
        alt.Chart(source)
        .mark_point()
        .encode(
            x="Horsepower",
            y="Miles_per_Gallon",
        )
    )

    text = (
        alt.Chart(source)
        .mark_text(align="right")
        .encode(alt.Text("Horsepower:N", title=dict(text="Horsepower", align="right")))
    )

    return points | text


def chart_example_invalid_channel_and_condition():
    selection = alt.selection_point()
    return (
        alt.Chart(data.barley())
        .mark_circle()
        .add_params(selection)
        .encode(
            color=alt.condition(selection, alt.value("red"), alt.value("green")),
            invalidChannel=None,
        )
    )


def chart_example_invalid_y_option():
    return (
        alt.Chart(data.barley())
        .mark_bar()
        .encode(
            x=alt.X("variety", unknown=2),
            y=alt.Y("sum(yield)", stack="asdf"),
        )
    )


def chart_example_invalid_y_option_value():
    return (
        alt.Chart(data.barley())
        .mark_bar()
        .encode(
            x=alt.X("variety"),
            y=alt.Y("sum(yield)", stack="asdf"),
        )
    )


def chart_example_invalid_y_option_value_with_condition():
    return (
        alt.Chart(data.barley())
        .mark_bar()
        .encode(
            x="variety",
            y=alt.Y("sum(yield)", stack="asdf"),
            opacity=alt.condition("datum.yield > 0", alt.value(1), alt.value(0.2)),
        )
    )


@pytest.mark.parametrize(
    "chart_func, expected_error_message",
    [
        (
            chart_example_invalid_y_option,
            r"schema.channels.X.*"
            + r"Additional properties are not allowed \('unknown' was unexpected\)",
        ),
        (
            chart_example_invalid_y_option_value,
            r"schema.channels.Y.*"
            + r"'asdf' is not one of \['zero', 'center', 'normalize'\].*"
            + r"'asdf' is not of type 'null'.*'asdf' is not of type 'boolean'",
        ),
        (
            chart_example_layer,
            r"api.VConcatChart.*"
            + r"Additional properties are not allowed \('width' was unexpected\)",
        ),
        (
            chart_example_invalid_y_option_value_with_condition,
            r"schema.channels.Y.*"
            + r"'asdf' is not one of \['zero', 'center', 'normalize'\].*"
            + r"'asdf' is not of type 'null'.*'asdf' is not of type 'boolean'",
        ),
        (
            chart_example_hconcat,
            r"schema.core.TitleParams.*"
            + r"\{'text': 'Horsepower', 'align': 'right'\} is not of type 'string'.*"
            + r"\{'text': 'Horsepower', 'align': 'right'\} is not of type 'array'",
        ),
        (
            chart_example_invalid_channel_and_condition,
            r"schema.core.Encoding->encoding.*"
            + r"Additional properties are not allowed \('invalidChannel' was unexpected\)",
        ),
    ],
)
def test_chart_validation_errors(chart_func, expected_error_message):
    # DOTALL flag makes that a dot (.) also matches new lines
    pattern = re.compile(expected_error_message, re.DOTALL)
    # For some wrong chart specifications such as an unknown encoding channel,
    # Altair already raises a warning before the chart specifications are validated.
    # We can ignore these warnings as we are interested in the errors being raised
    # during validation which is triggered by to_dict
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        chart = chart_func()
    with pytest.raises(SchemaValidationError, match=pattern):
        chart.to_dict()


def test_serialize_numpy_types():
    m = MySchema(
        a={"date": np.datetime64("2019-01-01")},
        a2={"int64": np.int64(1), "float64": np.float64(2)},
        b2=np.arange(4),
    )
    out = m.to_json()
    dct = json.loads(out)
    assert dct == {
        "a": {"date": "2019-01-01T00:00:00"},
        "a2": {"int64": 1, "float64": 2},
        "b2": [0, 1, 2, 3],
    }


def test_to_dict_no_side_effects():
    # Tests that shorthands are expanded in returned dictionary when calling to_dict
    # but that they remain untouched in the chart object. Goal is to ensure that
    # the chart object stays unchanged when to_dict is called
    def validate_encoding(encoding):
        assert encoding.x["shorthand"] == "a"
        assert encoding.x["field"] is alt.Undefined
        assert encoding.x["type"] is alt.Undefined
        assert encoding.y["shorthand"] == "b:Q"
        assert encoding.y["field"] is alt.Undefined
        assert encoding.y["type"] is alt.Undefined

    data = pd.DataFrame(
        {
            "a": ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
            "b": [28, 55, 43, 91, 81, 53, 19, 87, 52],
        }
    )

    chart = alt.Chart(data).mark_bar().encode(x="a", y="b:Q")

    validate_encoding(chart.encoding)
    dct = chart.to_dict()
    validate_encoding(chart.encoding)

    assert "shorthand" not in dct["encoding"]["x"]
    assert dct["encoding"]["x"]["field"] == "a"

    assert "shorthand" not in dct["encoding"]["y"]
    assert dct["encoding"]["y"]["field"] == "b"
    assert dct["encoding"]["y"]["type"] == "quantitative"


def test_to_dict_expand_mark_spec():
    # Test that `to_dict` correctly expands marks to a dictionary
    # without impacting the original spec which remains a string
    chart = alt.Chart().mark_bar()
    assert chart.to_dict()["mark"] == {"type": "bar"}
    assert chart.mark == "bar"