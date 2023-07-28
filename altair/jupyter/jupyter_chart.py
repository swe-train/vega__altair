import anywidget
import traitlets
import pathlib
from dataclasses import dataclass
from typing import Any, Dict, List

import altair as alt
from altair.utils._vegafusion_data import using_vegafusion
from altair.vegalite.v5.schema.core import TopLevelSpec

_here = pathlib.Path(__file__).parent


@dataclass(frozen=True, eq=True)
class IndexSelection:
    """
    An IndexSelection represents the state of an Altair
    point selection (as constructed by alt.selection_point())
    when neither the fields nor encodings arguments are specified.

    The value field is a list of zero-based indices into the
    selected dataset.

    Note: These indices only apply to the input DataFrame
    for charts that do not include aggregations (e.g. a scatter chart).
    """

    name: str
    value: List[int]
    store: List[Dict[str, Any]]


@dataclass(frozen=True, eq=True)
class PointSelection:
    """
    A PointSelection represents the state of an Altair
    point selection (as constructed by alt.selection_point())
    when the fields or encodings arguments are specified.

    The value field is a list of dicts of the form:
        [{"dim1": 1, "dim2": "A"}, {"dim1": 2, "dim2": "BB"}]

    where "dim1" and "dim2" are dataset columns and the dict values
    correspond to the specific selected values.
    """

    name: str
    value: List[Dict[str, Any]]
    store: List[Dict[str, Any]]


@dataclass(frozen=True, eq=True)
class IntervalSelection:
    """
    An IntervalSelection represents the state of an Altair
    interval selection (as constructed by alt.selection_interval()).

    The value field is a dict of the form:
        {"dim1": [0, 10], "dim2": ["A", "BB", "CCC"]}

    where "dim1" and "dim2" are dataset columns and the dict values
    correspond to the selected range.
    """

    name: str
    value: Dict[str, list]
    store: List[Dict[str, Any]]


class JupyterChart(anywidget.AnyWidget):
    _esm = _here / "js" / "index.js"
    _css = r"""
    .vega-embed {
        /* Make sure action menu isn't cut off */
        overflow: visible;
    }
    """

    # Public traitlets
    chart = traitlets.Instance(TopLevelSpec)
    spec = traitlets.Dict().tag(sync=True)
    selections = traitlets.Dict()
    params = traitlets.Dict().tag(sync=True)
    debounce_wait = traitlets.Float(default_value=10).tag(sync=True)

    # Internal selection traitlets
    _selection_types = traitlets.Dict()
    _selection_watches = traitlets.List().tag(sync=True)
    _selections = traitlets.Dict().tag(sync=True)

    # Internal param traitlets
    _param_watches = traitlets.List().tag(sync=True)

    def __init__(self, chart: TopLevelSpec, debounce_wait: int = 10, **kwargs: Any):
        """
        Jupyter Widget for displaying and updating Altair Charts, and
        retrieving selection and parameter values

        Parameters
        ----------
        chart: Chart
            Altair Chart instance
        debounce_wait: int
             Debouncing wait time in milliseconds
        """
        super().__init__(chart=chart, debounce_wait=debounce_wait, **kwargs)

    def set_params(self, **kwargs: Any):
        """
        Update one or more of a Chart's (non-selection) parameters.
        The parameters that are eligible for update are stored in
        the params property of the JupyterChart.

        Parameters
        ----------
        kwargs
            Parameter name and value pairs
        """
        updates = []
        new_params = dict(self.params)
        for name, value in kwargs.items():
            if name not in self.params:
                raise ValueError(f"No param named {name}")

            updates.append(
                {
                    "name": name,
                    "value": value,
                }
            )

            new_params[name] = value

        # Update params directly so that they are set immediately
        # after this function returns (rather than waiting for round
        # trip through front-end)
        self.params = new_params

        # Send param update message
        self.send({"type": "setParams", "updates": updates})

    @traitlets.observe("chart")
    def _on_change_chart(self, change):
        """
        Internal callback function that updates the JupyterChart's internal
        state when the wrapped Chart instance changes
        """
        new_chart = change.new

        params = getattr(new_chart, "params", [])
        selection_watches = []
        selection_types = {}
        param_watches = []
        initial_params = {}
        initial_selections = {}

        if params is not alt.Undefined:
            for param in new_chart.params:
                select = getattr(param, "select", alt.Undefined)

                if select != alt.Undefined:
                    if not isinstance(select, dict):
                        select = select.to_dict()

                    select_type = select["type"]
                    if select_type == "point":
                        if not (
                            select.get("fields", None) or select.get("encodings", None)
                        ):
                            # Point selection with no associated fields or encodings specified.
                            # This is an index-based selection
                            selection_types[param.name] = "index"
                        else:
                            selection_types[param.name] = "point"
                    elif select_type == "interval":
                        selection_types[param.name] = "interval"
                    else:
                        raise ValueError(f"Unexpected selection type {select.type}")
                    selection_watches.append(param.name)
                    initial_selections[param.name] = {"value": None, "store": []}
                else:
                    param_watches.append(param.name)
                    clean_value = param.value if param.value != alt.Undefined else None
                    initial_params[param.name] = clean_value

        # Update properties all together
        with self.hold_sync():
            if using_vegafusion():
                self.spec = new_chart.to_dict(format="vega")
            else:
                self.spec = new_chart.to_dict()
            self._selection_types = selection_types
            self._selection_watches = selection_watches
            self._selections = initial_selections
            self.params = initial_params
            self._param_watches = param_watches

    @traitlets.observe("_selections")
    def _on_change_selections(self, change):
        """
        Internal callback function that updates the JupyterChart's public
        selections traitlet in response to changes that the JavaScript logic
        makes to the internal _selections traitlet.
        """
        new_selections = {}
        for selection_name, selection_dict in change.new.items():
            value = selection_dict["value"]
            store = selection_dict["store"]
            selection_type = self._selection_types[selection_name]
            if selection_type == "index":
                if value is None:
                    indices = []
                else:
                    points = value.get("vlPoint", {}).get("or", [])
                    indices = [p["_vgsid_"] - 1 for p in points]
                new_selections[selection_name] = IndexSelection(
                    name=selection_name, value=indices, store=store
                )
            elif selection_type == "point":
                if value is None:
                    points = []
                else:
                    points = value.get("vlPoint", {}).get("or", [])
                new_selections[selection_name] = PointSelection(
                    name=selection_name, value=points, store=store
                )
            elif selection_type == "interval":
                if value is None:
                    value = {}
                new_selections[selection_name] = IntervalSelection(
                    name=selection_name, value=value, store=store
                )

        self.selections = new_selections